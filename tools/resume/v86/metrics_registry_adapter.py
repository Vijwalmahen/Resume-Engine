#!/usr/bin/env python3
"""
An explicit, narrow adaptation boundary for alternative metrics-registry
shapes.

`metrics_audit` consumes ONE canonical shape (see
master/examples/metrics_registry.example.json)::

    {"metrics": [{"id", "owner_id", "statement", "verified", "source"}]}

A deployment may keep its verified measurements in a different shape. The
one alternative supported here is an entity-keyed registry whose items
carry the measurement as structured fields rather than as prose::

    {"items": [{"id", "name", "entity", "description",
                "value", "unit", "source", "verified"}]}

The ONLY thing this module does is translate that shape into the
canonical one. It deliberately does NOT relax any provenance rule, and
it is built around one refusal:

    An entity is linked to a resume-content group ONLY through an
    explicit, operator-supplied mapping. A registry entity whose name
    merely resembles (or exactly equals) a project or employer name in
    the candidate's runtime context is NEVER linked on that basis.

A name-only resemblance is not evidence of ownership. Two projects can
share a word; an entity label can be a programme name rather than a
resume entry; a renamed entry silently re-points. Guessing there would
manufacture the exact "this measurement belongs to that bullet" claim
the audit exists to establish. So an unmapped entity produces no metric
at all, and `metrics_audit` reports it as `not_evaluated` with a reason.

The mapping (`entity_content_map`) is a plain dict, supplied by the
caller, from registry entity label to the resume-content group that owns
it::

    {
      "<entity label>": {
        "owner_id": "<runtime context experience[]/projects[] id>",
        "final_bullets": ["<payload key>", ...]      # optional
      }
    }

`owner_id` is resolved by `metrics_audit` against the runtime context
exactly as a canonical metric's own `owner_id` would be -- so a
non-existent or duplicated id still yields `not_evaluated`, never a
guess. The optional `final_bullets` path names where THIS deployment's
resume payload keeps that group's shipped bullets, for payload shapes
whose key names differ from the canonical
`experience_alternate_bullets` / `selected_project.bullets`.

Measurements are carried across as structured `measurements` tokens
built from the item's own `value`/`unit` fields using
`metrics_audit`'s own normalization, so value/unit boundaries (including
word units such as `4 hours`) behave identically to the canonical path
and no measurement is ever re-derived by parsing prose.

Nothing here fabricates a metric, infers a unit, or upgrades an
unverified item.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import metrics_audit  # noqa: E402

CANONICAL_KEY = "metrics"
ALTERNATE_KEY = "items"


def detect_shape(registry: Optional[dict]) -> str:
    """`"canonical"`, `"entity_items"`, `"empty"` or `"unknown"`."""
    if not isinstance(registry, dict) or not registry:
        return "empty"
    if isinstance(registry.get(CANONICAL_KEY), list):
        return "canonical"
    if isinstance(registry.get(ALTERNATE_KEY), list):
        return "entity_items"
    return "unknown"


def _measurement_tokens(value, unit) -> list:
    """Build the comparable measurement token(s) from the item's own
    structured fields. Returns `[]` when the value is not a number --
    a missing or non-numeric value is never guessed at."""
    if value is None or isinstance(value, bool):
        return []
    text = str(value).strip()
    if not text:
        return []
    unit_text = "" if unit is None else str(unit).strip()
    # Reuse the canonical extractor so the token spelling, the
    # thousands-separator handling and the unit boundaries are identical
    # to the prose path -- there is exactly one definition of what a
    # measurement token looks like.
    tokens = metrics_audit.extract_measurements(f"{text} {unit_text}".strip())
    return tokens


def adapt_entity_items(
    registry: dict,
    entity_content_map: Optional[dict] = None,
) -> tuple:
    """Translate an entity-keyed registry into the canonical shape.

    Returns `(canonical_registry, report)`. `report` is::

        {
          "shape": "entity_items",
          "items_seen": <int>,
          "adapted": <int>,
          "unmapped": [{"metric_id", "entity", "reason"}],
          "skipped":  [{"metric_id", "entity", "reason"}],
          "map_entries": <int>,
        }

    `unmapped` and `skipped` are both surfaced by the caller so an item
    that produced no canonical metric is reported as `not_evaluated`
    rather than silently vanishing.
    """
    entity_content_map = entity_content_map or {}
    items = registry.get(ALTERNATE_KEY) or []

    metrics: list = []
    content_map: dict = {}
    unmapped: list = []
    skipped: list = []

    for item in items:
        if not isinstance(item, dict):
            continue
        metric_id = str(item.get("id") or item.get("name") or "").strip() or "<unnamed metric>"
        entity = str(item.get("entity") or "").strip()

        if not entity:
            unmapped.append({
                "metric_id": metric_id, "entity": None,
                "reason": "item declares no entity, so it cannot be linked to a resume-content group",
            })
            continue

        mapping = entity_content_map.get(entity)
        if mapping is None:
            unmapped.append({
                "metric_id": metric_id, "entity": entity,
                "reason": (
                    "no explicit entity-to-content mapping for this entity; a name-only "
                    "resemblance to a resume entry is never treated as an ownership link"
                ),
            })
            continue

        if isinstance(mapping, str):
            mapping = {"owner_id": mapping}
        if not isinstance(mapping, dict):
            unmapped.append({
                "metric_id": metric_id, "entity": entity,
                "reason": "the mapping entry for this entity is not a usable owner/content record",
            })
            continue

        owner_id = str(mapping.get("owner_id") or "").strip()
        if not owner_id:
            unmapped.append({
                "metric_id": metric_id, "entity": entity,
                "reason": "the mapping entry for this entity declares no owner_id",
            })
            continue

        measurements = _measurement_tokens(item.get("value"), item.get("unit"))
        if not measurements:
            skipped.append({
                "metric_id": metric_id, "entity": entity,
                "reason": (
                    "the item carries no numeric value, so there is no measurement to audit "
                    "(no value is inferred)"
                ),
            })
            continue

        statement = str(item.get("description") or item.get("name") or "").strip()

        metrics.append({
            "id": metric_id,
            "owner_id": owner_id,
            # Kept for the report's evidence only. The audit uses the
            # structured `measurements` below, never this prose, so a
            # description that happens to contain other numbers cannot
            # widen what counts as the metric.
            "statement": statement,
            "measurements": measurements,
            "verified": item.get("verified") is True,
            "source": item.get("source"),
            "entity": entity,
        })

        final_bullets = mapping.get("final_bullets")
        if final_bullets:
            content_map[owner_id] = {"final_bullets": list(final_bullets)}

    report = {
        "shape": "entity_items",
        "items_seen": len(items),
        "adapted": len(metrics),
        "unmapped": unmapped,
        "skipped": skipped,
        "map_entries": len(entity_content_map),
    }
    return {CANONICAL_KEY: metrics}, content_map, report


def normalize_registry(
    registry: Optional[dict],
    entity_content_map: Optional[dict] = None,
) -> tuple:
    """Return `(canonical_registry, content_map, report)` for any
    supported registry shape.

    A canonical registry passes straight through untouched. An
    entity-keyed registry is adapted. Anything else yields an empty
    registry and a report saying so -- `metrics_audit` then reports
    `not_evaluated`, which is the correct answer for a shape this
    boundary does not understand.
    """
    shape = detect_shape(registry)

    if shape == "canonical":
        return registry, {}, {
            "shape": "canonical", "items_seen": len(registry.get(CANONICAL_KEY) or []),
            "adapted": len(registry.get(CANONICAL_KEY) or []),
            "unmapped": [], "skipped": [], "map_entries": 0,
        }

    if shape == "entity_items":
        return adapt_entity_items(registry, entity_content_map)

    return {CANONICAL_KEY: []}, {}, {
        "shape": shape,
        "items_seen": 0, "adapted": 0, "unmapped": [], "skipped": [],
        "map_entries": len(entity_content_map or {}),
        "reason": (
            "metrics registry is empty"
            if shape == "empty"
            else "metrics registry shape is not recognised by this adaptation boundary"
        ),
    }


def merge_into_audit(audit: dict, adaptation: dict) -> dict:
    """Fold the adaptation outcome back into an `audit_metrics` result.

    An item this boundary could not link produces no canonical metric,
    so `audit_metrics` never sees it. Without this merge it would
    simply vanish between the two stages -- which would understate how
    many measurements went unchecked. Every caller that adapts a
    registry should pass its audit result through here so unlinked and
    skipped items stay visible as `not_evaluated` rows carrying their
    own reason."""
    if not adaptation or adaptation.get("shape") == "canonical":
        return audit

    audit = dict(audit)
    audit["registry_adaptation"] = {
        key: adaptation.get(key)
        for key in ("shape", "items_seen", "adapted", "map_entries", "reason")
        if adaptation.get(key) is not None
    }

    extra = []
    for bucket in ("unmapped", "skipped"):
        for row in adaptation.get(bucket) or []:
            extra.append({
                "metric_id": row.get("metric_id"),
                "owner_id": None,
                "entity": row.get("entity"),
                "status": "not_evaluated",
                "reason": row.get("reason"),
                "measurements": [],
                "measurements_present": [],
                "source_bullet_index": None,
                "final_bullet_count": 0,
            })

    if not extra:
        return audit

    results = list(audit.get("results") or []) + extra
    summary = dict(audit.get("summary") or
                   {"omitted": 0, "present": 0, "not_evaluated": 0, "not_applicable": 0})
    summary["not_evaluated"] = summary.get("not_evaluated", 0) + len(extra)
    audit["results"] = results
    audit["summary"] = summary
    audit["metrics_considered"] = adaptation.get("items_seen", audit.get("metrics_considered", 0))
    # An adapted registry that linked nothing at all is still an
    # evaluated audit -- it evaluated every item and concluded, honestly,
    # that none could be linked.
    if audit.get("status") != "EVALUATED" and adaptation.get("items_seen"):
        audit["status"] = "EVALUATED"
        audit["reason"] = None
    return audit
