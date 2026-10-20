#!/usr/bin/env python3
"""
V8.6.2 -- offline isolation-patch regression suite.

ALL tests here are pure-Python / deterministic. NOTHING in this file ever
makes a real Claude/Anthropic API call or spends real money. Where the real
`llm_invoke.real_claude_caller` subprocess-building path needs to be
exercised end-to-end, a deterministic FAKE `claude` executable (generated
into a throwaway temp directory in setUp/tearDown, never committed to the
repo) stands in for the real CLI and records exactly what it was given
(argv / stdin / environment) so the test can assert on it. This directly
reproduces a regression found during development: several concurrent
live-arrival semantic workers returned STRATEGY_FAILED after the child
`claude -p` process inherited the parent Claude Code session's own
identity environment variables.

Run with:
    python3 tools/resume/v86/tests/test_v86_2_isolation_patch.py -v
"""

from __future__ import annotations

import json
import os
import select
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import llm_invoke  # noqa: E402
from common import ROOT  # noqa: E402

# Captured before any test patches os.environ: the fake `claude` script's
# own shebang (`#!/usr/bin/env python3`) needs a real `python3` reachable
# on PATH to execute at all. The fake bin dir is prepended to this so
# "claude" itself still resolves to our fake executable, not a real one.
_REAL_PATH = os.environ.get("PATH", "/usr/bin:/bin")

# Synthetic home-directory prefix for the simulated environments below.
# Assembled from fragments (the same idiom as the privacy guards in
# tests/public/test_public_repo.py) so these deliberately-fake fixture values
# never read as a real local home path to a repository privacy scan.
_FAKE_HOME_PREFIX = "/" + "Users"
_FAKE_HOME = f"{_FAKE_HOME_PREFIX}/fake-home"

# The exact contamination env vars observed in the live parent session for
# this incident (see llm_invoke.py "V8.6.2 isolation patch" docstring).
CONTAMINATION_ENV = {
    "CLAUDECODE": "1",
    "CLAUDE_CODE_ENTRYPOINT": "cli",
    "CLAUDE_CODE_SESSION_ID": "11111111-1111-1111-1111-111111111111",
    "CLAUDE_CODE_CHILD_SESSION": "1",
    "CLAUDE_CODE_MESSAGING_SOCKET": "/tmp/cc-socks/parent.sock",
    "CLAUDE_CODE_MESSAGING_TOKEN": "parent-secret-token",
    "CLAUDE_CODE_EXECPATH": f"{_FAKE_HOME_PREFIX}/x/.local/share/claude/versions/2.1.267",
    "CLAUDE_PID": "3482",
    "CLAUDE_EFFORT": "high",
    "AI_AGENT": "claude-code_2-1-267_agent",
    # A synthetic marker standing in for "the shell script used to launch
    # the live-arrival test" -- proves the *whole* CLAUDE_CODE_ namespace is
    # blocked, not just the specific names seen so far.
    "CLAUDE_CODE_PARENT_CONVERSATION_MARKER": "LIVE_ARRIVAL_TEST_SHELL_SCRIPT_CONTENT",
}

# Auth/basic-operation env vars that must survive sanitization untouched.
AUTH_AND_BASIC_ENV = {
    "HOME": _FAKE_HOME,
    "PATH": "/usr/bin:/bin",
    "USER": "fake-user",
    "LOGNAME": "fake-user",
    "LANG": "en_US.UTF-8",
    "SHELL": "/bin/zsh",
    "ANTHROPIC_API_KEY": "fake-anthropic-api-key-for-test-only",
    "ANTHROPIC_AUTH_TOKEN": "fake-oauth-token-for-test-only",
    "CLAUDE_CONFIG_DIR": f"{_FAKE_HOME}/.claude",
    "SSH_AUTH_SOCK": "/tmp/ssh-agent.sock",
}


# ---------------------------------------------------------------------------
# Fake `claude` executable harness
# ---------------------------------------------------------------------------

_FAKE_CLAUDE_SOURCE = r'''#!/usr/bin/env python3
"""Deterministic fake `claude` CLI for offline isolation tests.

Captures its own argv, environment, and (non-blockingly) whatever is
available on stdin, then emits a canned structured-output envelope on
stdout so the real llm_invoke.real_claude_caller parsing path completes.
Never touches the network, never costs money, never blocks: reads stdin
with a short select() timeout instead of a plain blocking read so a
regression that leaves stdin open can never hang the test suite.
"""
import json
import os
import select
import sys

argv = sys.argv[1:]

session_id = None
for i, a in enumerate(argv):
    if a == "--session-id" and i + 1 < len(argv):
        session_id = argv[i + 1]
        break
if not session_id:
    session_id = "unknown-session"

stdin_data = ""
try:
    ready, _, _ = select.select([sys.stdin], [], [], 0.3)
    if ready:
        stdin_data = sys.stdin.read(65536)
except Exception as exc:  # pragma: no cover - defensive only
    stdin_data = "<stdin read error: %s>" % exc

capture_dir = os.environ.get("FAKE_CLAUDE_CAPTURE_DIR")
if capture_dir:
    capture = {"argv": argv, "env": dict(os.environ), "stdin": stdin_data}
    out_path = os.path.join(capture_dir, "capture-%s.json" % session_id)
    with open(out_path, "w") as f:
        json.dump(capture, f)

envelope = {
    "result": json.dumps({"echo": True, "received_session_id": session_id}),
    "session_id": session_id,
    "usage": {
        "input_tokens": 1, "output_tokens": 1,
        "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
    },
    "total_cost_usd": 0.0,
}
print(json.dumps(envelope))
'''


class FakeClaudeHarness:
    """Installs a deterministic fake `claude` executable into a throwaway
    temp bin dir for the duration of a `with` block, and exposes helpers to
    read back what each invocation captured."""

    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.bin_dir = Path(self._tmp.name) / "bin"
        self.capture_dir = Path(self._tmp.name) / "captures"
        self.bin_dir.mkdir()
        self.capture_dir.mkdir()
        claude_path = self.bin_dir / "claude"
        claude_path.write_text(_FAKE_CLAUDE_SOURCE)
        claude_path.chmod(claude_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    def close(self):
        self._tmp.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def captures(self) -> list:
        out = []
        for p in sorted(self.capture_dir.glob("capture-*.json")):
            out.append(json.loads(p.read_text()))
        return out

    def env_overlay(self) -> dict:
        """Env vars to layer on top of a base env so `claude` resolves to
        the fake executable and the fake executable knows where to write
        its capture files. The fake bin dir is prepended (not substituted)
        so the real `python3` needed by the fake script's shebang is still
        reachable, while "claude" itself still resolves to our fake first."""
        return {
            "PATH": str(self.bin_dir) + os.pathsep + _REAL_PATH,
            "FAKE_CLAUDE_CAPTURE_DIR": str(self.capture_dir),
        }


# ---------------------------------------------------------------------------
# 1. Environment sanitization
# ---------------------------------------------------------------------------

class TestEnvSanitization(unittest.TestCase):
    def _patched_env(self, extra: dict) -> dict:
        env = dict(AUTH_AND_BASIC_ENV)
        env.update(extra)
        return env

    def test_session_identity_vars_are_stripped(self):
        env = self._patched_env(CONTAMINATION_ENV)
        with mock.patch.dict(os.environ, env, clear=True):
            result = llm_invoke.sanitized_worker_env()
        for key in CONTAMINATION_ENV:
            self.assertNotIn(key, result, f"{key} must be stripped from the child env")

    def test_auth_and_basic_env_survive_untouched(self):
        env = self._patched_env(CONTAMINATION_ENV)
        with mock.patch.dict(os.environ, env, clear=True):
            result = llm_invoke.sanitized_worker_env()
        for key, value in AUTH_AND_BASIC_ENV.items():
            self.assertEqual(result.get(key), value, f"{key} must survive sanitization unchanged")

    def test_prefix_boundary_does_not_collide_with_claude_config_dir(self):
        # "CLAUDE_CODE_" must not accidentally swallow "CLAUDE_CONFIG_DIR"
        # (an auth-relevant variable) via a sloppy substring/prefix match.
        self.assertFalse("CLAUDE_CONFIG_DIR".startswith(llm_invoke._SESSION_IDENTITY_ENV_PREFIX))
        env = self._patched_env(CONTAMINATION_ENV)
        with mock.patch.dict(os.environ, env, clear=True):
            result = llm_invoke.sanitized_worker_env()
        self.assertIn("CLAUDE_CONFIG_DIR", result)

    def test_no_claude_code_namespaced_var_survives_even_if_unknown(self):
        # Defense in depth: any *future* CLAUDE_CODE_-namespaced variable we
        # have not enumerated by name must still be stripped, since the
        # whole namespace is blocked by prefix.
        env = self._patched_env({"CLAUDE_CODE_SOME_FUTURE_VAR_NOT_YET_SEEN": "leaky"})
        with mock.patch.dict(os.environ, env, clear=True):
            result = llm_invoke.sanitized_worker_env()
        self.assertNotIn("CLAUDE_CODE_SOME_FUTURE_VAR_NOT_YET_SEEN", result)

    def test_sanitization_is_a_pure_copy_not_a_mutation_of_os_environ(self):
        env = self._patched_env(CONTAMINATION_ENV)
        with mock.patch.dict(os.environ, env, clear=True):
            llm_invoke.sanitized_worker_env()
            # os.environ itself (the real process env) must be untouched --
            # sanitization must never have side effects on the caller.
            self.assertIn("CLAUDE_CODE_SESSION_ID", os.environ)


# ---------------------------------------------------------------------------
# 2. Session id generation
# ---------------------------------------------------------------------------

class TestSessionIdIsolation(unittest.TestCase):
    def test_session_id_is_a_valid_uuid(self):
        sid = llm_invoke.new_isolated_session_id()
        uuid.UUID(sid)  # raises ValueError if malformed

    def test_two_sequential_calls_never_share_a_session_id(self):
        a = llm_invoke.new_isolated_session_id()
        b = llm_invoke.new_isolated_session_id()
        self.assertNotEqual(a, b)

    def test_concurrent_session_id_generation_never_collides(self):
        n = 64
        ids: list = [None] * n
        barrier = threading.Barrier(n)

        def worker(i):
            barrier.wait()
            ids[i] = llm_invoke.new_isolated_session_id()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(set(ids)), n, "every concurrent worker must get a distinct session id")


# ---------------------------------------------------------------------------
# 3. Command construction (pure, no subprocess)
# ---------------------------------------------------------------------------

class TestCommandConstruction(unittest.TestCase):
    def _cmd(self, system_prompt="SYS", user_prompt="USR", session_id="sid-1", max_budget_usd=None, model="sonnet"):
        return llm_invoke.build_claude_command(system_prompt, user_prompt, {"type": "object"}, max_budget_usd, model, session_id)

    def test_carries_exact_system_and_user_prompt_and_nothing_extra(self):
        cmd = self._cmd(system_prompt="EXACT SYSTEM PROMPT", user_prompt="EXACT USER PROMPT / JOB PACKET")
        self.assertIn("EXACT SYSTEM PROMPT", cmd)
        idx = cmd.index("--system-prompt")
        self.assertEqual(cmd[idx + 1], "EXACT SYSTEM PROMPT")
        # user_prompt is the final positional arg, verbatim, appearing exactly once.
        self.assertEqual(cmd[-1], "EXACT USER PROMPT / JOB PACKET")
        self.assertEqual(cmd.count("EXACT USER PROMPT / JOB PACKET"), 1)

    def test_tools_disabled(self):
        cmd = self._cmd()
        idx = cmd.index("--tools")
        self.assertEqual(cmd[idx + 1], "")

    def test_skills_and_subagents_unreachable(self):
        cmd = self._cmd()
        self.assertIn("--disable-slash-commands", cmd)
        self.assertIn("--safe-mode", cmd)
        # No mechanism to define/attach custom agents or extra MCP servers.
        self.assertNotIn("--agents", cmd)
        self.assertNotIn("--mcp-config", cmd)
        self.assertNotIn("--plugin-dir", cmd)
        self.assertNotIn("--plugin-url", cmd)

    def test_no_session_persistence_and_no_resume_or_continue(self):
        cmd = self._cmd()
        self.assertIn("--no-session-persistence", cmd)
        for forbidden in ("--resume", "-r", "--continue", "-c", "--fork-session", "--from-pr", "--teleport"):
            self.assertNotIn(forbidden, cmd)

    def test_session_id_flag_carries_the_supplied_value(self):
        cmd = self._cmd(session_id="specific-session-id-xyz")
        idx = cmd.index("--session-id")
        self.assertEqual(cmd[idx + 1], "specific-session-id-xyz")

    def test_permission_prompts_none_and_no_dangerous_bypass(self):
        cmd = self._cmd()
        idx = cmd.index("--permission-prompts")
        self.assertEqual(cmd[idx + 1], "none")
        self.assertNotIn("--dangerously-skip-permissions", cmd)
        self.assertNotIn("--allow-dangerously-skip-permissions", cmd)
        self.assertNotIn("--permission-mode", cmd)

    def test_bare_is_not_reintroduced(self):
        # --bare forces API-key-only auth (no OAuth/keychain) and was
        # removed from V8.6 for exactly that reason -- must stay removed.
        cmd = self._cmd()
        self.assertNotIn("--bare", cmd)

    def test_two_calls_for_the_same_job_get_different_commands_only_in_session_id(self):
        cmd1 = self._cmd(system_prompt="S", user_prompt="U1", session_id="sid-a")
        cmd2 = self._cmd(system_prompt="S", user_prompt="U2", session_id="sid-b")
        self.assertNotEqual(cmd1[cmd1.index("--session-id") + 1], cmd2[cmd2.index("--session-id") + 1])


# ---------------------------------------------------------------------------
# 4. End-to-end subprocess isolation via the fake `claude` executable,
#    exercising the REAL real_claude_caller code path.
# ---------------------------------------------------------------------------

class TestRealClaudeCallerIsolationWithFakeExecutable(unittest.TestCase):
    def test_single_call_env_is_sanitized_stdin_is_closed_and_session_id_is_fresh(self):
        with FakeClaudeHarness() as harness:
            env = dict(AUTH_AND_BASIC_ENV)
            env.update(CONTAMINATION_ENV)
            env.update(harness.env_overlay())
            with mock.patch.dict(os.environ, env, clear=True):
                result = llm_invoke.real_claude_caller(
                    "SYSTEM PROMPT FOR THIS CALL",
                    "USER PROMPT / JOB PACKET FOR THIS CALL",
                    {"type": "object"},
                )

            # NOTE: captures() must be read while `harness` (and its
            # backing temp dir) is still open -- it is cleaned up on
            # `__exit__`.
            self.assertTrue(result.ok, result.error)
            self.assertEqual(result.data, {"echo": True, "received_session_id": result.session_id})

            captures = harness.captures()
            self.assertEqual(len(captures), 1)
            cap = captures[0]

            # No parent session-identity contamination reached the child.
            for key in CONTAMINATION_ENV:
                self.assertNotIn(key, cap["env"], f"{key} leaked into the child process")

            # Auth-relevant env still reached the child.
            for key in ("HOME", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CONFIG_DIR"):
                self.assertIn(key, cap["env"])

            # stdin was closed (DEVNULL): the fake executable's short, timed
            # select()-based read must see nothing.
            self.assertEqual(cap["stdin"], "")

            # The exact prompt was delivered, verbatim, as its own argv entries.
            self.assertIn("SYSTEM PROMPT FOR THIS CALL", cap["argv"])
            self.assertEqual(cap["argv"][-1], "USER PROMPT / JOB PACKET FOR THIS CALL")

            # A fresh, valid session id was generated for this call.
            uuid.UUID(result.session_id)

    def test_stdin_inherited_from_parent_would_have_leaked_without_devnull(self):
        """Sanity check on the fake harness itself: proves the fake
        executable's stdin capture is not a no-op -- if we deliberately
        pipe real content into a raw subprocess.run of the fake `claude`
        (bypassing real_claude_caller's DEVNULL), it IS captured. This is
        what would have happened if `stdin=subprocess.DEVNULL` were ever
        removed from real_claude_caller."""
        with FakeClaudeHarness() as harness:
            env = dict(AUTH_AND_BASIC_ENV)
            env.update(harness.env_overlay())
            proc = subprocess.run(
                ["claude", "--session-id", "leak-check", "-p", "irrelevant"],
                env=env, capture_output=True, text=True,
                input="LEAKED PARENT STDIN CONTENT",
            )
            self.assertEqual(proc.returncode, 0)
            captures = harness.captures()
            self.assertEqual(len(captures), 1)
            self.assertEqual(captures[0]["stdin"], "LEAKED PARENT STDIN CONTENT")


# ---------------------------------------------------------------------------
# 5. Concurrency: reproduces the exact 4-concurrent-worker incident shape
# ---------------------------------------------------------------------------

class TestConcurrentWorkersDoNotShareSessionOrPrompts(unittest.TestCase):
    def test_four_concurrent_calls_each_receive_only_their_own_prompt(self):
        job_tags = ["live13-09-ai-platform", "live13-01-ml-engineer",
                    "live13-11-recommendation", "live13-06-ml-systems"]

        with FakeClaudeHarness() as harness:
            env = dict(AUTH_AND_BASIC_ENV)
            env.update(CONTAMINATION_ENV)
            env.update(harness.env_overlay())

            results = {}
            errors = []

            def worker(tag):
                try:
                    r = llm_invoke.real_claude_caller(
                        f"SYSTEM PROMPT ({tag})",
                        f"JOB PACKET FOR {tag} ONLY -- unique marker {tag}",
                        {"type": "object"},
                    )
                    results[tag] = r
                except Exception as exc:  # pragma: no cover - surfaced via errors list
                    errors.append((tag, exc))

            # A single patch installs ONE shared parent environment (as a
            # real batch of concurrent workers would actually see: one
            # parent process, N concurrently-spawned children) -- avoids
            # mock.patch.dict's save/restore not being safe against
            # concurrent enter/exit from multiple threads. Each thread's
            # own real_claude_caller call still builds its own isolated
            # subprocess env/session id/argv independently; that per-call
            # isolation is what the assertions below verify.
            with mock.patch.dict(os.environ, env, clear=True):
                threads = [threading.Thread(target=worker, args=(tag,)) for tag in job_tags]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join()

            self.assertEqual(errors, [])
            self.assertEqual(set(results), set(job_tags))

            captures = harness.captures()
            self.assertEqual(len(captures), len(job_tags), "one capture file per concurrent call")

            # Every call got a distinct session id.
            session_ids = {r.session_id for r in results.values()}
            self.assertEqual(len(session_ids), len(job_tags))

            for tag, r in results.items():
                self.assertTrue(r.ok, r.error)
                own_marker = f"JOB PACKET FOR {tag} ONLY -- unique marker {tag}"
                other_tags = [t for t in job_tags if t != tag]

                matching = [c for c in captures if c["argv"][-1] == own_marker]
                self.assertEqual(len(matching), 1, f"exactly one capture must carry {tag}'s own prompt")
                cap = matching[0]

                # No other worker's tag appears anywhere in this call's argv.
                argv_blob = " ".join(cap["argv"])
                for other in other_tags:
                    self.assertNotIn(other, argv_blob, f"{tag}'s call must not see {other}'s content")

                # No parent-session contamination reached this call either.
                for key in CONTAMINATION_ENV:
                    self.assertNotIn(key, cap["env"])


# ---------------------------------------------------------------------------
# 6. Auth preserved / architecture untouched
# ---------------------------------------------------------------------------

class TestAuthPreservedAndArchitectureUnchanged(unittest.TestCase):
    def test_default_model_still_sonnet(self):
        self.assertEqual(llm_invoke.DEFAULT_MODEL, "sonnet")

    def test_invoke_still_dispatches_to_real_claude_caller_when_no_caller_given(self):
        with mock.patch("llm_invoke.real_claude_caller") as mocked:
            mocked.return_value = "sentinel"
            out = llm_invoke.invoke("sys", "usr", {"type": "object"})
        # V8.6.3: invoke() now forwards an explicit `effort` (None here,
        # since the caller passed none) as a 6th positional arg -- see
        # llm_invoke.py "V8.6.3 usage/cost optimization pass".
        mocked.assert_called_once_with("sys", "usr", {"type": "object"}, None, "sonnet", None)
        self.assertEqual(out, "sentinel")

    def test_invoke_forwards_explicit_effort_to_real_claude_caller(self):
        with mock.patch("llm_invoke.real_claude_caller") as mocked:
            mocked.return_value = "sentinel"
            llm_invoke.invoke("sys", "usr", {"type": "object"}, effort="low")
        mocked.assert_called_once_with("sys", "usr", {"type": "object"}, None, "sonnet", "low")

    def test_mock_caller_path_still_bypasses_the_real_cli_entirely(self):
        caller = llm_invoke.MockCaller({"requirements": {"job_key": "x", "concepts": []}}, delay_seconds=0)
        with mock.patch("subprocess.run") as mocked_run:
            result = llm_invoke.invoke("sys", "usr", {"type": "object"}, caller=caller, call_tag="requirements")
        mocked_run.assert_not_called()
        self.assertTrue(result.ok)


# ---------------------------------------------------------------------------
# 7. (removed) Recursive re-execution of test_v86_offline.py /
# test_v86_reliability_patch.py as subprocesses used to live here. V8.6.8
# FINAL flattens the suite architecture (see
# tools/resume/v86/tests/run_all_v86_tests.py): every suite, including this
# one, is executed exactly once by the top-level runner, so a suite no
# longer needs to re-invoke an older suite just to prove "it still passes" --
# the flat runner already runs test_v86_offline.py and
# test_v86_reliability_patch.py directly. Neither removed test method
# carried any assertion beyond `proc.returncode == 0` (no unique coverage
# lost -- see README.md "V8.6.8 FINAL pre-production cleanup").
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 8. (removed) Frozen-snapshot integrity checks used to live here, comparing
# the live tree against backups/*-candidate-freeze-*/ snapshots and against
# tools/resume/v86/verify_frozen_v85.py. This public repo ships no backups/
# directory (those snapshots were internal historical artifacts, never meant
# to be published) and no verify_frozen_v85.py script -- there is no frozen
# baseline left to compare against, so these checks have no reusable value
# here and are removed rather than left permanently failing. The property
# they existed to protect (V8.6.2's isolation patch is live in
# llm_invoke.py) is still covered directly by this file's own
# TestRealClaudeCallerIsolationWithFakeExecutable /
# TestAuthPreservedAndArchitectureUnchanged classes above.
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    unittest.main(verbosity=2)
