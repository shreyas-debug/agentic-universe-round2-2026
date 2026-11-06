"""Canonical Cohort Engine.

Implements shared cohort definitions across detection, experimentation, and governance.
Evaluates leads against priority-ordered cohort where-clauses (AND across features, OR within feature list).
"""
from __future__ import annotations

from typing import Any

# Allowed feature domain from simulator
FEATURES: dict[str, list[str]] = {
    "lang": ["en", "hi", "ta", "te", "kn", "bn", "mr", "gu"],
    "city_tier": ["1", "2", "3"],
    "age_band": ["18-30", "31-45", "46-60", "60+"],
    "value_band": ["low", "mid", "high"],
}


def matches_where(where: dict[str, list[str]], features: dict[str, str]) -> bool:
    """Return True if features match the where clause.

    AND across features, OR within each feature's list.
    """
    for feat, allowed_vals in where.items():
        val = features.get(feat)
        if val is None or val not in allowed_vals:
            return False
    return True


def assign_cohort(cohorts: list[dict[str, Any]], features: dict[str, str]) -> str:
    """Assign a lead to the first matching cohort, or 'default' if none match."""
    for c in cohorts:
        cid = c.get("id")
        where = c.get("where", {})
        if cid and matches_where(where, features):
            return cid
    return "default"


def generate_standard_cohorts(workflow_id: str, lang_mix: dict[str, float] | None = None) -> list[dict[str, Any]]:
    """Generate canonical cohorts for a workflow.

    Creates high-leverage cohorts based on age and prominent languages,
    ensuring each cohort has enough volume to support statistical experimentation.
    """
    cohorts: list[dict[str, Any]] = []

    # 1. Senior cohort (often has distinct voice/pacing preferences)
    cohorts.append({
        "id": "senior",
        "where": {"age_band": ["60+"]}
    })

    # 2. Young cohort (often has different engagement habits)
    cohorts.append({
        "id": "young",
        "where": {"age_band": ["18-30"]}
    })

    # 3. Prominent language cohorts if workflow has diverse language mix
    if lang_mix:
        for lang, share in sorted(lang_mix.items(), key=lambda x: -x[1]):
            if share >= 0.15 and len(cohorts) < 8:
                cohorts.append({
                    "id": f"lang_{lang}",
                    "where": {"lang": [lang]}
                })

    # 4. Metro / Tier 1 cohort
    cohorts.append({
        "id": "tier1",
        "where": {"city_tier": ["1"]}
    })

    return cohorts
