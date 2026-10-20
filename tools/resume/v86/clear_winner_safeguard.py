#!/usr/bin/env python3
"""
Deterministic clear-winner safeguard for selectable-project selection.

This module does NOT reimplement, duplicate, or modify the deterministic
selector (tools/resume/v8/select_project.py). It is a pure,
post-processing function over that script's already-computed, already
evidence-attributed `rankings` output (see
deterministic_pipeline.ensure_project_selection, the only call site).
Every field this module reads (`required_match_count`,
`critical_high_required_match_count`, `required_score`, `score`,
`total_match_count`) was computed by the frozen selector using ONLY evidence
records whose `entity` is declared in that exact project's
`evidence_entities` policy list, plus that project's own runtime
name/technologies/bullets -- evidence ownership is therefore inherited
structurally, never recomputed or re-derived here. This module cannot award
a project credit for another project's (or a protected canonical project's)
evidence, because it never looks at evidence at all -- only at scores
another script already attributed correctly.

Problem this addresses: the deterministic selector only binds
deterministically ("BINDING_REQUIRED_COVERAGE") when the top-ranked
candidate has at least one INCREMENTAL REQUIRED-concept match no other
candidate also has (see the ranking contract in select_project.py).
Whenever every
candidate's `required_match_count` is 0 -- e.g. because every REQUIRED
concept a candidate might have supported is already covered by fixed/
protected baseline content, or genuinely not owned by any real candidate --
the selector falls back to `MODEL_CHOICE` and hands the ENTIRE unfiltered
candidate pool to semantic call 2, even when the pool's own OPTIONAL/
preferred-skill and overall-relevance signal (still real, still owned,
still per-candidate) overwhelmingly favors one project. Call 2 (an LLM call)
is then free to pick a materially weaker project anyway.

This safeguard closes exactly that gap: when `MODEL_CHOICE` would otherwise
apply, but one candidate's deterministic score dominates every other
candidate by a conservative margin, `selection_mode` is upgraded to
`BINDING_REQUIRED_COVERAGE` (the SAME accepted enum value
tools/resume/v8/validate_project_selection.py already knows how to
validate -- inventing a new mode string would make that guard reject
every job that hit it). Everything else about the
selection report is preserved; only `selection_mode`,
`selected_project_key`, `selected_runtime_name`,
`selected_required_score`, and `selected_required_matches` are overwritten
to describe the newly-bound winner, and one new, purely additive diagnostic
key (`clear_winner_safeguard`) is attached recording why (or why not) the
safeguard fired -- for offline replay/audit, not consumed by any frozen
script.

Ambiguous, tied, or no-signal cases are conservatively left exactly as the
frozen selector produced them (ordinary `MODEL_CHOICE`) -- see
`evaluate_clear_winner`'s thresholds and docstring.
"""

from __future__ import annotations

import copy
from typing import Optional

# ---------------------------------------------------------------------------
# Conservative clear-winner thresholds.
#
# These only ever apply inside the MODEL_CHOICE branch, where -- BY
# CONSTRUCTION of the frozen selector's own sort key
# (-required_match_count, -critical_high_required_match_count,
# -required_score, -score, -total_match_count, tie_break_order) -- every
# candidate's required_match_count is already 0 (rankings[0] has the
# largest required_match_count of any row; MODEL_CHOICE only happens when
# THAT is 0, so no row can be positive). The only remaining, genuinely
# per-candidate-owned signal left to discriminate on is `score` /
# `total_match_count`, both built exclusively from OPTIONAL/preferred
# matches and each candidate's own runtime text + safe evidence records.
# ---------------------------------------------------------------------------

# Winner must have matched at least this many distinct JD concepts (via its
# OWN evidence) before it is trusted to bind anything -- a 1-match "winner"
# is not a signal, it is noise.
MIN_TOTAL_MATCHES = 3

# Winner's weighted score (PRIORITY_WEIGHT_* sum) must clear this floor.
MIN_SCORE = 30

# Winner must out-match the runner-up by at least this many distinct
# concepts. Rules out "6 matches vs 5 matches" as a false clear-winner.
MIN_MATCH_COUNT_MARGIN = 2

# Winner's score must be at least this many times the runner-up's score
# (only checked when the runner-up's score is > 0 -- a runner-up scoring
# exactly 0 cannot fail a ratio check by construction).
MIN_SCORE_RATIO = 2.0


def _dominance_ok(winner: dict, runner_up: Optional[dict]) -> bool:
    """Defense-in-depth sanity check: the winner must never trail the
    runner-up on any REQUIRED-concept tier. Given the frozen selector's own
    sort order this can never actually happen (rankings[0] always leads or
    ties on every one of these keys), but this function does not assume its
    caller always hands it a `rankings[0]`/`rankings[1]` pair straight from
    that exact sort -- it re-checks explicitly so the ordering contract in
    the module docstring is enforced here too, not just implied."""
    if runner_up is None:
        return True
    if runner_up.get("required_match_count", 0) > winner.get("required_match_count", 0):
        return False
    if runner_up.get("critical_high_required_match_count", 0) > winner.get(
        "critical_high_required_match_count", 0
    ):
        return False
    if runner_up.get("required_score", 0) > winner.get("required_score", 0):
        return False
    return True


def evaluate_clear_winner(rankings: list) -> Optional[dict]:
    """Given the deterministic selector's `rankings` (already sorted best-first,
    already evidence-attributed per project), decide whether the top
    candidate is a CLEAR winner over every other candidate.

    Returns None for: no candidates, no signal at all (winner's
    total_match_count is 0), a near tie, or an exact tie. Returns a
    diagnostics dict (margin, both candidates' counts/scores) only when
    every conservative condition holds.
    """
    if not rankings:
        return None

    winner = rankings[0]
    runner_up = rankings[1] if len(rankings) > 1 else None

    if not _dominance_ok(winner, runner_up):
        return None

    if winner.get("total_match_count", 0) < MIN_TOTAL_MATCHES:
        return None  # no signal / too weak to trust

    if winner.get("score", 0) < MIN_SCORE:
        return None

    if runner_up is not None:
        winner_matches = winner.get("total_match_count", 0)
        runner_up_matches = runner_up.get("total_match_count", 0)
        if (winner_matches - runner_up_matches) < MIN_MATCH_COUNT_MARGIN:
            return None  # near tie or exact tie on match count

        winner_score = winner.get("score", 0)
        runner_up_score = runner_up.get("score", 0)
        if runner_up_score > 0 and winner_score < runner_up_score * MIN_SCORE_RATIO:
            return None  # near tie or exact tie on score

    return {
        "winner_project_key": winner["project_key"],
        "winner_runtime_name": winner["runtime_name"],
        "winner_total_match_count": winner.get("total_match_count", 0),
        "winner_score": winner.get("score", 0),
        "runner_up_project_key": runner_up.get("project_key") if runner_up else None,
        "runner_up_total_match_count": runner_up.get("total_match_count", 0) if runner_up else 0,
        "runner_up_score": runner_up.get("score", 0) if runner_up else 0,
        "match_count_margin": winner.get("total_match_count", 0)
        - (runner_up.get("total_match_count", 0) if runner_up else 0),
        "score_margin": winner.get("score", 0) - (runner_up.get("score", 0) if runner_up else 0),
    }


def apply_clear_winner_safeguard(selection: dict) -> dict:
    """Post-process one project_selection.json report (already loaded
    as a dict). Never mutates the input.

    - If `selection_mode` is already `BINDING_REQUIRED_COVERAGE` (the
      selector itself found an incremental required-concept winner), this
      is a no-op -- the selector's own binding decision is never
      second-guessed, and the ORIGINAL object is returned unchanged (so
      callers can skip a needless rewrite via an identity check).
    - If `selection_mode` is `MODEL_CHOICE` and no clear winner is found,
      returns a copy with one new diagnostic key
      (`clear_winner_safeguard: {"applied": False, ...}`) and nothing else
      changed -- selection_mode/selected_* stay exactly `MODEL_CHOICE`/None.
    - If `selection_mode` is `MODEL_CHOICE` and a clear winner IS found,
      returns a copy with `selection_mode` upgraded to
      `BINDING_REQUIRED_COVERAGE` and `selected_project_key` /
      `selected_runtime_name` / `selected_required_score` /
      `selected_required_matches` set to describe that winner (mirroring
      exactly what the frozen selector itself would have written had it
      found this project's coverage on its own), plus the same diagnostic
      key with `"applied": True` and the margin evidence.
    """
    if selection.get("selection_mode") != "MODEL_CHOICE":
        return selection

    rankings = selection.get("rankings") or []
    clear = evaluate_clear_winner(rankings)

    result = copy.deepcopy(selection)

    if clear is None:
        result["clear_winner_safeguard"] = {
            "applied": False,
            "reason": "NO_CLEAR_WINNER_MARGIN",
            "thresholds": _thresholds(),
        }
        return result

    winner_row = next(
        row for row in rankings if row["project_key"] == clear["winner_project_key"]
    )

    result["selection_mode"] = "BINDING_REQUIRED_COVERAGE"
    result["selected_project_key"] = winner_row["project_key"]
    result["selected_runtime_name"] = winner_row["runtime_name"]
    result["selected_required_score"] = winner_row.get("required_score", 0)
    result["selected_required_matches"] = winner_row.get("required_matches", [])
    result["clear_winner_safeguard"] = {
        "applied": True,
        "reason": "CLEAR_WINNER_OVERALL_EVIDENCE_MARGIN",
        "margin": clear,
        "thresholds": _thresholds(),
    }
    return result


def _thresholds() -> dict:
    return {
        "min_total_matches": MIN_TOTAL_MATCHES,
        "min_score": MIN_SCORE,
        "min_match_count_margin": MIN_MATCH_COUNT_MARGIN,
        "min_score_ratio": MIN_SCORE_RATIO,
    }
