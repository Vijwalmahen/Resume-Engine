#!/usr/bin/env python3
"""
Injectable Apify client interface with an offline fake transport.

What this module deliberately does NOT contain
----------------------------------------------
The actor that produced this workspace's historical job scrapes is
UNIDENTIFIED. No actor id, no actor input field name, and no actor result
field name is recorded anywhere in this repository, and none is guessed here:
copying a plausible-looking schema from another project would produce a
client that fails at the first live run, after the run has already been paid
for. So this module ships the *shape* of a client and nothing else:

  - `ActorConfig` must be supplied, in full, by the caller. There is no
    default actor id, no default input mapping, no default result mapping.
  - `validate_live_config()` fails closed with `ConfigurationError`, naming
    every missing setting, before any run is attempted.
  - `LiveTransport` refuses to execute at all while `LIVE_EXECUTION_ENABLED`
    is False, which is its value in this version and in every test.

Token handling
--------------
`APIFY_TOKEN` is read from the environment only inside the live path, only at
the moment it would be used, and is never stored on an object, never printed,
never logged, and never serialized. `token_present()` answers the only
question the rest of the pipeline ever needs ("is one configured?") and
returns a bool, not the value.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable, Optional

TOKEN_ENV_VAR = "APIFY_TOKEN"

# Hard gate. Live execution stays off in this version: the actor contract is
# unknown (see the module docstring), so there is nothing to execute yet.
LIVE_EXECUTION_ENABLED = False


class ConfigurationError(Exception):
    """Raised when a live run is attempted without a complete actor contract."""


class LiveExecutionDisabled(Exception):
    """Raised when a live run is attempted while LIVE_EXECUTION_ENABLED is False."""


class TransportError(Exception):
    """A transport-level failure (timeout, refusal, bad payload). Callers map
    this to the SCRAPE_FAILED intake status."""


@dataclass
class ActorConfig:
    """The complete, explicit contract for one Apify actor run.

    Every field must be supplied by whoever configures the actor. Nothing
    here has a working default, by design.

    actor_id:        the actor to run, e.g. "<account>/<actor-name>".
    input_mapping:   canonical intake input name -> this actor's input field
                     name. Intake only ever supplies "urls" (a list of
                     canonical job URLs); an actor that takes its targets
                     under a different field name is accommodated here, not
                     by renaming intake's own vocabulary.
    static_input:    any additional literal actor input fields (proxy config,
                     result limits, ...) the actor requires, verbatim.
    max_cost_usd:    explicit ceiling for one run. Required.
    timeout_seconds: explicit request/run timeout. Required.
    """

    actor_id: Optional[str] = None
    input_mapping: dict = field(default_factory=dict)
    static_input: dict = field(default_factory=dict)
    max_cost_usd: Optional[float] = None
    timeout_seconds: Optional[int] = None

    def missing_settings(self) -> list:
        missing = []
        if not (self.actor_id or "").strip():
            missing.append("actor_id")
        if "urls" not in self.input_mapping or not str(self.input_mapping.get("urls") or "").strip():
            missing.append("input_mapping['urls'] (this actor's input field name for the target URLs)")
        if self.max_cost_usd is None:
            missing.append("max_cost_usd")
        if self.timeout_seconds is None:
            missing.append("timeout_seconds")
        return missing

    def build_input(self, urls) -> dict:
        """Map intake's canonical inputs onto this actor's own field names.
        Raises ConfigurationError rather than falling back to a guessed name."""
        missing = self.missing_settings()
        if missing:
            raise ConfigurationError(
                "Apify actor configuration is incomplete: " + ", ".join(missing)
            )
        payload = dict(self.static_input)
        payload[self.input_mapping["urls"]] = list(urls)
        return payload

    def __repr__(self) -> str:  # never let a token-adjacent dump happen by accident
        return (
            f"ActorConfig(actor_id={self.actor_id!r}, "
            f"input_fields={sorted(self.input_mapping)!r}, "
            f"max_cost_usd={self.max_cost_usd!r}, timeout_seconds={self.timeout_seconds!r})"
        )


def token_present(env: Optional[dict] = None) -> bool:
    """True when APIFY_TOKEN is set and non-empty. Returns a bool — the token
    value itself never leaves this function."""
    env = os.environ if env is None else env
    return bool((env.get(TOKEN_ENV_VAR) or "").strip())


# Which sources the (unidentified) actor can actually handle is itself part of
# the missing contract. Until an actor is identified and its coverage stated,
# this set is empty, so validate_live_config() refuses every source.
SUPPORTED_SOURCES_PLACEHOLDER: set = set()


def validate_live_config(config: ActorConfig, sources, env: Optional[dict] = None) -> None:
    """Fail closed before any live run: actor id, input mapping, token, source
    support, cost ceiling and timeout must ALL be explicitly configured."""
    problems = [f"ActorConfig.{m}" for m in config.missing_settings()]
    if not token_present(env):
        problems.append(f"{TOKEN_ENV_VAR} environment variable is not set")
    unsupported = sorted({s for s in sources if s not in SUPPORTED_SOURCES_PLACEHOLDER})
    if unsupported:
        problems.append(
            "no actor support has been declared for source(s): " + ", ".join(unsupported)
        )
    if problems:
        raise ConfigurationError(
            "Live Apify execution is not configured:\n  - " + "\n  - ".join(problems)
        )


class Transport:
    """Interface: given an actor input payload, return a list of raw result
    records (whatever shape the actor emits — mapping happens in
    `extractors.py`)."""

    def run(self, actor_id: str, actor_input: dict, timeout_seconds: int) -> list:
        raise NotImplementedError


class LiveTransport(Transport):
    """The real transport. Disabled: there is no actor contract to call yet."""

    def run(self, actor_id: str, actor_input: dict, timeout_seconds: int) -> list:
        if not LIVE_EXECUTION_ENABLED:
            raise LiveExecutionDisabled(
                "Live Apify execution is disabled in this version. The historical actor is "
                "unidentified: its id, input field names and result field names must be "
                "determined and configured (ActorConfig + extractors.ResultFieldMap) before "
                "a live run can be attempted."
            )
        # Intentionally unreachable while the gate above is False. The real
        # implementation belongs here, written against the actual actor's
        # documented contract — never against a guessed one.
        raise LiveExecutionDisabled("no live Apify implementation is present in this version")


class FakeTransport(Transport):
    """Offline transport for tests and `--dry-run`-adjacent development.

    `responses` maps a canonical URL to either a raw result record (any dict
    shape — it is fed through the same `extractors` mapping a real actor
    result would be) or to an Exception instance, which is raised as a
    transport failure for that URL. A URL with no entry comes back as no
    result at all, which intake reports as SCRAPE_FAILED.
    """

    def __init__(self, responses: Optional[dict] = None, url_field: str = "url"):
        self.responses = dict(responses or {})
        self.url_field = url_field
        self.calls: list = []

    def run(self, actor_id: str, actor_input: dict, timeout_seconds: int) -> list:
        self.calls.append({"actor_id": actor_id, "input": actor_input,
                           "timeout_seconds": timeout_seconds})
        urls = []
        for value in actor_input.values():
            if isinstance(value, (list, tuple)):
                urls = list(value)
                break
        results = []
        for url in urls:
            entry = self.responses.get(url)
            if entry is None:
                continue
            if isinstance(entry, Exception):
                raise TransportError(f"{type(entry).__name__}: {entry}")
            record = dict(entry)
            record.setdefault(self.url_field, url)
            results.append(record)
        return results


@dataclass
class RunOutcome:
    """One scrape attempt's outcome, per URL."""

    results_by_url: dict = field(default_factory=dict)
    failed_urls: dict = field(default_factory=dict)   # url -> error string
    charged_usd: float = 0.0


class ApifyClientShim:
    """Injectable client: a transport plus an actor contract.

    The transport is always supplied by the caller. There is no default, so
    nothing can accidentally fall back to a live call.
    """

    def __init__(self, transport: Transport, config: ActorConfig,
                 url_field: str = "url", cost_estimator: Optional[Callable] = None):
        if transport is None:
            raise ConfigurationError("a transport must be injected explicitly")
        self.transport = transport
        self.config = config
        self.url_field = url_field
        self.cost_estimator = cost_estimator

    @property
    def live(self) -> bool:
        return isinstance(self.transport, LiveTransport)

    def run_urls(self, urls, sources=(), env: Optional[dict] = None,
                 max_cost_usd: Optional[float] = None,
                 timeout_seconds: Optional[int] = None) -> RunOutcome:
        urls = list(urls)
        if not urls:
            return RunOutcome()

        if self.live:
            validate_live_config(self.config, sources, env=env)

        ceiling = self.config.max_cost_usd if max_cost_usd is None else max_cost_usd
        if ceiling is None:
            raise ConfigurationError("an explicit max_cost_usd ceiling is required")
        timeout = self.config.timeout_seconds if timeout_seconds is None else timeout_seconds
        if timeout is None:
            raise ConfigurationError("an explicit timeout_seconds is required")

        estimated = float(self.cost_estimator(urls)) if self.cost_estimator else 0.0
        if estimated > float(ceiling):
            raise ConfigurationError(
                f"estimated run cost ${estimated:.4f} exceeds the configured ceiling ${float(ceiling):.4f}"
            )

        actor_input = self.config.build_input(urls)
        outcome = RunOutcome(charged_usd=estimated)
        try:
            raw_results = self.transport.run(self.config.actor_id, actor_input, int(timeout))
        except TransportError as exc:
            for url in urls:
                outcome.failed_urls[url] = str(exc)
            return outcome

        for record in raw_results or []:
            if not isinstance(record, dict):
                continue
            url = record.get(self.url_field)
            if url in outcome.results_by_url or url is None:
                continue
            outcome.results_by_url[url] = record

        for url in urls:
            if url not in outcome.results_by_url and url not in outcome.failed_urls:
                outcome.failed_urls[url] = "actor returned no result for this URL"
        return outcome

    def __repr__(self) -> str:
        return f"ApifyClientShim(transport={type(self.transport).__name__}, config={self.config!r})"
