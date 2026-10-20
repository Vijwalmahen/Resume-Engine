#!/usr/bin/env python3
"""
V8.6 -- narrow, schema-bounded, tool-free Claude CLI invocation.

Every semantic/correction call in V8.6 goes through `invoke_claude()` here.
Design goals (see tools/resume/v86/README.md Section "Permission audit" for
the full rationale, derived from `claude --help` on the installed CLI --
flags were not guessed):

  * `--tools ""`        disable every built-in tool. The worker cannot use
                         Bash, Read, Write, or spawn subagents. It cannot
                         explore the repository, inspect infrastructure, or
                         re-invoke the deterministic third-project selector.
  * `--disable-slash-commands`  no skills are reachable, so the worker
                         cannot invoke jobapp-resume-tailor / finalize-resume
                         / linkedin-job-scraper etc. even by name.
  * `--safe-mode`       (V8.6.2) disables every ambient customization --
                         CLAUDE.md auto-discovery, hooks, plugins, MCP
                         servers, custom commands/agents -- while leaving
                         normal account auth (OAuth/keychain), model
                         selection, and permissions working exactly as they
                         do for an ordinary logged-in `claude` invocation.
                         Unlike `--bare` (see "V8.6.2 isolation patch"
                         below), this does NOT force API-key-only auth, so
                         it does not reintroduce the "Not logged in" break
                         that got `--bare` removed from V8.6.
  * `--session-id <uuid>`  (V8.6.2) a freshly generated, never-reused UUID
                         handed to every single call, so no two calls --
                         concurrent workers, sequential semantic/correction
                         calls for the same job, or anything inherited from
                         the parent -- can ever share a session identity.
  * `--permission-prompts none`  belt-and-braces: if anything ever *would*
                         prompt (it shouldn't, with zero tools), it is
                         auto-denied rather than hanging a batch worker.
  * `--no-session-persistence`  the call is never written to disk and can
                         never be `--resume`d/`--continue`d later.
  * `--json-schema`     the model's structured output is validated against
                         an explicit JSON Schema -- no free-form prose to
                         parse.
  * `--output-format json`  gives back cost/usage metadata alongside the
                         structured result, which V8.6 timing instrumentation
                         records per Section 16.

`--dangerously-skip-permissions` / `--allow-dangerously-skip-permissions`
are never used here and are not needed: with zero tools granted, there is
nothing left that permission-checking would gate.

--------------------------------------------------------------------------
V8.6.2 isolation patch (subprocess environment/session isolation)
--------------------------------------------------------------------------
ROOT CAUSE: `real_claude_caller` ran `claude -p ...` via `subprocess.run`
with no `env=` override and no `stdin=` override. The child therefore
inherited, byte-for-byte, the PARENT Claude Code process's own
session-identity environment variables -- `CLAUDE_CODE_SESSION_ID`,
`CLAUDE_CODE_CHILD_SESSION`, `CLAUDE_CODE_MESSAGING_SOCKET` /
`CLAUDE_CODE_MESSAGING_TOKEN`, `CLAUDECODE`, `CLAUDE_CODE_ENTRYPOINT` -- the
exact set the Claude Code runtime uses to recognize a spawned `claude`
process as a CHILD/SUBAGENT SESSION of an existing conversation rather than
a fresh, standalone one. A worker launched with those variables still set
is not isolated at all: it is indistinguishable, to the runtime, from a
child of whatever the parent interactive session happened to be doing at
that moment. That is the mechanism strongly evidenced by a regression found
during development: most live-arrival workers in one run returned
STRATEGY_FAILED, each claiming to see "a shell/bash test-harness script" in
"the human turn in this conversation" -- i.e. content from the PARENT
Claude Code conversation (the live-arrival test launch script), not the
job_packet.json actually handed to `invoke()`. This could not be a
prompt-construction bug: `semantic_worker.py` builds `user_prompt` from
only the job packet, and the diagnostic evidence
(`output/<job>/jd_requirements.json`, `job_packet.json`) shows valid
packets existed for every affected job.

FIX (all in `real_claude_caller`, the sole real-CLI call site):
  1. `sanitized_worker_env()` -- the child's environment is a copy of this
     process's own environment with ONLY the known Claude Code
     session-identity variables removed (exact names, plus the whole
     `CLAUDE_CODE_` namespace). Auth-relevant variables (`ANTHROPIC_*`,
     `CLAUDE_CONFIG_DIR`, `HOME`/keychain, `PATH`, ...) and everything else
     are passed through untouched -- auth must keep working exactly as it
     does today.
  2. `new_isolated_session_id()` -- a fresh `uuid.uuid4()` passed via
     `--session-id` on every call (belt-and-braces on top of #1: even if
     some other propagation path we have not seen reintroduces a shared
     identifier, this still forces a distinct session per call).
  3. `--safe-mode` added to the command (see above) -- hooks/CLAUDE.md/
     plugins/custom commands & agents are disabled at the CLI level too,
     not just relied upon via env sanitization.
  4. `stdin=subprocess.DEVNULL` explicitly on the `subprocess.run` call --
     the child can no longer read whatever the parent shell happened to
     have buffered on its own stdin.
  5. `build_claude_command()` factored out as a pure function (no
     subprocess execution) so the exact argv can be asserted on in offline
     tests without shelling out.

This does not change the 2-semantic-call architecture, the queue/refill/
live-arrival scanning in `run_batch.py`, or any correction/validation
logic. See `tools/resume/v86/tests/test_v86_2_isolation_patch.py`.

This module also provides a MOCK caller used exclusively by the offline
test suite (tools/resume/v86/tests/). The mock never invokes the `claude`
binary and never makes a network call; it returns deterministic canned
JSON so the whole pipeline can be exercised for free.

--------------------------------------------------------------------------
V8.6.3 usage/cost optimization pass (per-call-type `--effort`)
--------------------------------------------------------------------------
`claude --help` on the installed CLI (inspected directly, no flag guessed)
exposes `--effort <low|medium|high|xhigh|max>` ("Effort level for the
current session"). V8.6/V8.6.1/V8.6.2 never passed this flag, so every
call -- the narrow, mechanical JD-extraction call, the tailored-authoring
call, AND every mechanical correction round -- silently ran at the CLI's
own default, "high" (Sonnet 5 also runs adaptive thinking on by default,
which cannot be disabled without accepting known failure modes -- see
tools/resume/v86/README.md). Sonnet 5's thinking/reasoning tokens are
billed and reported as part of `usage.output_tokens` regardless of
`display` visibility settings, so a call whose visible JSON is a few
hundred bytes can still report several thousand output tokens; this is the
root cause identified in the V8.6.3 usage audit for CALL 1
(jd_requirements extraction) and correction rounds -- both are narrow,
schema-bounded, mechanical tasks with no need for the deepest reasoning
depth. `real_claude_caller` now accepts an explicit, per-call `effort`
argument and always passes it through via `--effort` -- nothing is ever
left to an implicit CLI default again. Call-specific choices (each
independently overridable, none inherited from a parent env var per the
V8.6.2 sanitization policy) live at each call site:
tools/resume/v86/semantic_worker.py (`run_requirements_call` ->
`"low"`, `run_content_call` -> `"high"`, unchanged from the prior implicit
default) and tools/resume/v86/correction_worker.py (`run_correction_call`
-> `"low"`). CALL 2 (tailored resume/cover-letter authoring, the one call
that genuinely needs deep reasoning about JD fit, truthful evidence
selection, and prose quality) intentionally keeps the same effort depth it
already had; only the two mechanical call types are lowered.

--------------------------------------------------------------------------
V8.6.4 EXPERIMENTAL efficiency patch (single-variable CALL 2 effort probe)
--------------------------------------------------------------------------
V8.6.3 left `run_content_call` at `"high"` deliberately, without a real
(paid) call to confirm "high" reasoning depth is actually required for
that call's quality. V8.6.4 is a candidate-only experiment that changes
exactly ONE thing relative to V8.6.3:
`semantic_worker.run_content_call`'s `effort` default -> `"medium"`
(was `"high"`). CALL 1 (`"low"`) and correction (`"low"`) are unchanged.
No prompt text, schema, candidate_context slicing, architecture,
queue/refill/live-arrival behavior, correction limits, tracker behavior,
or final QA changed. This has NOT been benchmarked with a real call and
has NOT been promoted: `config/active_resume_pipeline.json` still names
V8.6.1 as `active_version`. See tools/resume/v86/README.md "V8.6.4
EXPERIMENTAL efficiency patch" for the full writeup and rollback path (any
configured frozen baseline this pass took before editing anything).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).resolve().parents[3]

DEFAULT_MODEL = "sonnet"


# ---------------------------------------------------------------------------
# V8.6.2 isolation helpers
# ---------------------------------------------------------------------------

# Exact env var names known (from this incident, plus `claude --help`) to
# carry Claude Code's OWN runtime/session identity -- never something a
# resume worker needs, and never an auth variable.
_SESSION_IDENTITY_ENV_EXACT = {
    "CLAUDECODE",
    "CLAUDE_PID",
    "CLAUDE_EFFORT",
    "AI_AGENT",
}
# Every session/messaging/runtime variable observed lives in this
# namespace (CLAUDE_CODE_SESSION_ID, CLAUDE_CODE_CHILD_SESSION,
# CLAUDE_CODE_MESSAGING_SOCKET, CLAUDE_CODE_MESSAGING_TOKEN,
# CLAUDE_CODE_ENTRYPOINT, CLAUDE_CODE_EXECPATH, CLAUDE_CODE_SAFE_MODE, ...).
# Auth lives elsewhere (ANTHROPIC_*, CLAUDE_CONFIG_DIR, HOME's keychain),
# so stripping this whole namespace never touches authentication.
_SESSION_IDENTITY_ENV_PREFIX = "CLAUDE_CODE_"


def sanitized_worker_env() -> dict:
    """Environment for the child `claude -p` process: a copy of this
    process's own environment (`os.environ`) with ONLY Claude Code's
    session-identity variables removed. Everything else -- PATH, HOME,
    ANTHROPIC_*, CLAUDE_CONFIG_DIR, locale, etc. -- passes through
    untouched, so normal account authentication keeps working exactly as
    it does for any other `claude` invocation on this machine."""
    env = dict(os.environ)
    for key in list(env):
        if key in _SESSION_IDENTITY_ENV_EXACT or key.startswith(_SESSION_IDENTITY_ENV_PREFIX):
            del env[key]
    return env


def new_isolated_session_id() -> str:
    """A fresh, never-reused session id for exactly one call."""
    return str(uuid.uuid4())


def build_claude_command(
    system_prompt: str,
    user_prompt: str,
    json_schema: dict,
    max_budget_usd: Optional[float],
    model: str,
    session_id: str,
    effort: Optional[str] = None,
) -> list:
    """Pure argv builder -- no subprocess execution, no environment access --
    so tests can assert on the exact command line without shelling out.
    `system_prompt` and `user_prompt` are passed through verbatim as their
    own discrete argv entries; nothing is concatenated, appended to, or
    read from any other source.

    `effort` (V8.6.3): one of "low"/"medium"/"high"/"xhigh"/"max", or None
    to omit the flag entirely (the pre-V8.6.3 behavior -- the CLI's own
    default). Every real call site now passes an explicit value; `None`
    remains supported so existing tests that build a command without an
    opinion on effort keep constructing the exact same argv as before."""
    cmd = [
        "claude",
        "-p",
        "--output-format", "json",
        "--model", model,
        "--session-id", session_id,
        "--system-prompt", system_prompt,
        "--json-schema", json.dumps(json_schema),
        "--tools", "",
        "--disable-slash-commands",
        "--safe-mode",
        "--permission-prompts", "none",
        "--no-session-persistence",
    ]
    if max_budget_usd is not None:
        cmd += ["--max-budget-usd", str(max_budget_usd)]
    if effort is not None:
        cmd += ["--effort", effort]
    cmd += [user_prompt]
    return cmd


@dataclass
class LlmCallResult:
    ok: bool
    data: Optional[dict]
    raw_text: str
    duration_ms: float
    model: str = DEFAULT_MODEL
    session_id: Optional[str] = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    error: Optional[str] = None


Caller = Callable[[str, str, dict, Optional[float]], LlmCallResult]


def _now_ms() -> float:
    import time
    return time.monotonic() * 1000.0


def real_claude_caller(
    system_prompt: str,
    user_prompt: str,
    json_schema: dict,
    max_budget_usd: Optional[float] = None,
    model: str = DEFAULT_MODEL,
    effort: Optional[str] = None,
) -> LlmCallResult:
    """Invoke the installed Claude CLI as a single, narrow, tool-free,
    session-isolated call (see "V8.6.2 isolation patch" above). `effort`
    (V8.6.3, see "V8.6.3 usage/cost optimization pass" above) is passed
    through to `--effort` verbatim; every real call site supplies one
    explicitly."""
    session_id = new_isolated_session_id()
    cmd = build_claude_command(system_prompt, user_prompt, json_schema, max_budget_usd, model, session_id, effort)

    t0 = _now_ms()
    proc = subprocess.run(
        cmd,
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=sanitized_worker_env(),
        stdin=subprocess.DEVNULL,
    )
    dt = round(_now_ms() - t0, 1)

    if proc.returncode != 0:
        return LlmCallResult(
            ok=False, data=None, raw_text=proc.stdout + proc.stderr,
            duration_ms=dt, model=model, error=f"claude exited {proc.returncode}",
        )

    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return LlmCallResult(
            ok=False, data=None, raw_text=proc.stdout, duration_ms=dt,
            model=model, error=f"non-JSON CLI output: {exc}",
        )

    result_field = envelope.get("result")
    try:
        data = json.loads(result_field) if isinstance(result_field, str) else result_field
    except (json.JSONDecodeError, TypeError) as exc:
        return LlmCallResult(
            ok=False, data=None, raw_text=proc.stdout, duration_ms=dt,
            model=model, error=f"structured result did not parse as JSON: {exc}",
        )

    usage = envelope.get("usage", {}) or {}
    return LlmCallResult(
        ok=True,
        data=data,
        raw_text=proc.stdout,
        duration_ms=dt,
        model=model,
        session_id=envelope.get("session_id"),
        input_tokens=usage.get("input_tokens", 0),
        output_tokens=usage.get("output_tokens", 0),
        cache_read_tokens=usage.get("cache_read_input_tokens", 0),
        cache_write_tokens=usage.get("cache_creation_input_tokens", 0),
        cost_usd=envelope.get("total_cost_usd", 0.0),
    )


class MockCaller:
    """Deterministic offline stand-in for `real_claude_caller`, used ONLY by
    tools/resume/v86/tests/. Never shells out, never costs money.

    `responses` maps a call-tag (see semantic_worker.py / correction_worker.py
    `call_tag` argument) to a fixed dict that is returned as `.data`.
    A callable may be supplied instead of a dict for dynamic per-job mocks.
    """

    def __init__(self, responses: dict, delay_seconds: float = 0.15):
        self.responses = responses
        self.calls: list = []
        self.delay_seconds = delay_seconds

    def __call__(self, system_prompt, user_prompt, json_schema, max_budget_usd=None, model=DEFAULT_MODEL, call_tag=None, effort=None):
        if self.delay_seconds:
            import time
            time.sleep(self.delay_seconds)
        self.calls.append(call_tag)
        entry = self.responses.get(call_tag)
        if entry is None:
            raise KeyError(f"MockCaller has no canned response for call_tag={call_tag!r}")
        data = entry(user_prompt) if callable(entry) else entry
        return LlmCallResult(
            ok=True,
            data=data,
            raw_text=json.dumps(data),
            duration_ms=1.0,
            model=model,
            session_id=f"mock-{call_tag}",
            input_tokens=500,
            output_tokens=500,
            cache_read_tokens=0,
            cache_write_tokens=0,
            cost_usd=0.0,
        )


def invoke(
    system_prompt: str,
    user_prompt: str,
    json_schema: dict,
    caller=None,
    call_tag: str = None,
    max_budget_usd: Optional[float] = None,
    model: str = DEFAULT_MODEL,
    effort: Optional[str] = None,
) -> LlmCallResult:
    """Single entry point used by semantic_worker.py / correction_worker.py.
    `caller` defaults to the real CLI; tests pass a MockCaller instance.
    `effort` (V8.6.3) is forwarded to the real CLI's `--effort`; MockCaller
    accepts and ignores it (offline tests never invoke the real CLI, so
    effort has no observable effect there).
    """
    if caller is None:
        return real_claude_caller(system_prompt, user_prompt, json_schema, max_budget_usd, model, effort)
    if isinstance(caller, MockCaller):
        return caller(system_prompt, user_prompt, json_schema, max_budget_usd, model, call_tag=call_tag, effort=effort)
    # plain callable(system_prompt, user_prompt, json_schema) -> LlmCallResult
    return caller(system_prompt, user_prompt, json_schema)
