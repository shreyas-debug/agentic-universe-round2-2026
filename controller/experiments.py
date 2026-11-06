"""Experimentation & Allocation Engine (C6 & C7).

Designs multi-armed bandit exploration/exploitation across welcome_message and voice,
evaluates statistical winners per cohort, respects guardrail KPIs, and detects decay.
"""
from __future__ import annotations

import copy
import logging
from collections import defaultdict
from typing import Any
from .stats import compare_proportions

logger = logging.getLogger(__name__)


class ExperimentManager:
    """Manages variant exploration, winner selection, and decay rollback per workflow."""

    def __init__(self, workflow_id: str, wf_spec: dict[str, Any]):
        self.workflow_id = workflow_id
        self.spec = wf_spec
        self.options = wf_spec.get("options", {})
        self.objective = wf_spec.get("objective", {})
        self.primary_kpi = self.objective.get("primary", "engagement_rate")
        self.guardrail = self.objective.get("guardrail", {})

        # Cohort -> Dimension (welcome_message/voice) -> Variant -> [dials, connected, engaged]
        self.stats: dict[str, dict[str, dict[str, list[int]]]] = defaultdict(
            lambda: defaultdict(lambda: defaultdict(lambda: [0, 0, 0]))
        )

        # Track active winners to monitor decay: (cohort, dim) -> winner_variant
        self.active_winners: dict[tuple[str, str], tuple[str, float]] = {}

    def record_call(
        self,
        cohort_id: str,
        variant: dict[str, str],
        connected: bool,
        engaged: bool,
    ):
        for dim in ("welcome_message", "voice"):
            val = variant.get(dim)
            if val:
                s = self.stats[cohort_id][dim][val]
                s[0] += 1  # dials
                if connected:
                    s[1] += 1  # connected
                    if engaged:
                        s[2] += 1  # engaged

    def plan_next_allocations(
        self,
        current_config: dict[str, Any],
        active_cohorts: list[dict[str, Any]],
        min_connected_samples: int = 200,
    ) -> tuple[dict[str, Any], dict[str, Any], float]:
        """Proposes an optimized config based on bandit evaluation.

        Returns:
            (new_config, diff, estimated_impact_score)
        """
        new_cfg = copy.deepcopy(current_config)
        # Welcome and voice act after pickup. A pickup-primary workflow needs
        # measured caller-ID evidence, not a noisy welcome/voice winner.
        if self.primary_kpi == "pickup_rate":
            return new_cfg, {}, 0.0
        new_cfg["cohorts"] = active_cohorts
        diff: dict[str, Any] = {}
        total_impact = 0.0
        modifications = 0

        cohort_names = ["default"] + [c["id"] for c in active_cohorts]

        for cid in cohort_names:
            if cid not in new_cfg["allocations"]:
                new_cfg["allocations"][cid] = {}

            for dim in ("welcome_message", "voice"):
                available_opts = self.options.get(dim, [])
                if len(available_opts) <= 1:
                    continue

                cohort_stats = self.stats[cid][dim]
                # Check if we have explored all options
                tried = {opt: cohort_stats[opt] for opt in available_opts if cohort_stats[opt][1] > 0}
                if len(tried) < len(available_opts):
                    # Still in exploration phase: allocate balanced traffic
                    cur_alloc = new_cfg["allocations"].get(cid, {}).get(dim, {})
                    uniform_weight = round(1.0 / len(available_opts), 4)
                    balanced_alloc = {opt: uniform_weight for opt in available_opts}
                    if cur_alloc != balanced_alloc:
                        new_cfg["allocations"][cid][dim] = balanced_alloc
                        diff[f"allocations.{cid}.{dim}"] = {"to": balanced_alloc, "reason": "uniform_exploration"}
                    continue

                # All options explored: evaluate candidate rates
                rates = {}
                for opt in available_opts:
                    dials, conn, eng = cohort_stats[opt]
                    # Check if sample size is sufficient
                    if conn >= min_connected_samples:
                        # Primary metric
                        rate = (eng / conn) if self.primary_kpi == "engagement_rate" else (conn / dials)
                        rates[opt] = (rate, conn, eng, dials)

                if len(rates) >= 2:
                    # Sort candidates by performance
                    sorted_opts = sorted(rates.items(), key=lambda x: -x[1][0])
                    best_opt, (best_rate, best_conn, best_eng, best_dials) = sorted_opts[0]
                    runner_up, (ru_rate, ru_conn, ru_eng, ru_dials) = sorted_opts[1]

                    # Check statistical significance between best and runner-up based on primary KPI
                    guardrail_passed = True
                    if self.primary_kpi == "engagement_rate":
                        delta, z, _ = compare_proportions(ru_eng, ru_conn, best_eng, best_conn)
                        if "pickup_rate_max_drop" in self.guardrail:
                            best_pickup = best_conn / max(1, best_dials)
                            ru_pickup = ru_conn / max(1, ru_dials)
                            max_drop = self.guardrail["pickup_rate_max_drop"]
                            if (ru_pickup - best_pickup) > max_drop:
                                guardrail_passed = False
                                logger.warning(
                                    "Workflow %s, cohort %s, %s: Variant %s violates guardrail pickup drop",
                                    self.workflow_id, cid, dim, best_opt
                                )
                    else:
                        delta, z, _ = compare_proportions(ru_conn, ru_dials, best_conn, best_dials)
                        if "engagement_rate_max_drop" in self.guardrail:
                            best_eng_rate = best_eng / max(1, best_conn)
                            ru_eng_rate = ru_eng / max(1, ru_conn)
                            max_drop = self.guardrail["engagement_rate_max_drop"]
                            if (ru_eng_rate - best_eng_rate) > max_drop:
                                guardrail_passed = False
                                logger.warning(
                                    "Workflow %s, cohort %s, %s: Variant %s violates guardrail engagement drop",
                                    self.workflow_id, cid, dim, best_opt
                                )

                    # Check for decay on previous winner
                    decay_key = (cid, dim)
                    if decay_key in self.active_winners:
                        prev_winner, historical_peak = self.active_winners[decay_key]
                        # If previous winner's current rate has decayed
                        if prev_winner in rates:
                            prev_curr_rate = rates[prev_winner][0]
                            if (historical_peak - prev_curr_rate) > 0.06:
                                logger.info(
                                    "Workflow %s, cohort %s, %s: Winner %s declined from %.3f to %.3f; fresh comparison required",
                                    self.workflow_id, cid, dim, prev_winner, historical_peak, prev_curr_rate
                                )
                                # Do not promote another arm using the z-score
                                # calculated for this winner. A fresh comparison
                                # is required at the next decision window.

                    # Conservative fixed threshold for repeated looks and
                    # several arms; no LLM participates in winner selection.
                    if z >= 3.3 and guardrail_passed:
                        # Exploit with safety margin (80% winner, 20% explore remaining)
                        exploit_alloc = {}
                        rem_opts = [o for o in available_opts if o != best_opt]
                        exploit_alloc[best_opt] = 0.80
                        rem_weight = round(0.20 / len(rem_opts), 4) if rem_opts else 0.0
                        for ro in rem_opts:
                            exploit_alloc[ro] = rem_weight
                        # Normalize
                        tot = sum(exploit_alloc.values())
                        exploit_alloc[best_opt] += round(1.0 - tot, 4)

                        cur_alloc = new_cfg["allocations"].get(cid, {}).get(dim, {})
                        if cur_alloc != exploit_alloc:
                            new_cfg["allocations"][cid][dim] = exploit_alloc
                            diff[f"allocations.{cid}.{dim}"] = {
                                "winner": best_opt,
                                "lift": round(delta, 4),
                                "z_score": round(z, 2),
                                "allocation": exploit_alloc,
                            }
                            self.active_winners[decay_key] = (best_opt, best_rate)
                            total_impact += min(0.3, max(0.05, delta))
                            modifications += 1

        avg_impact = round(total_impact / max(1, modifications), 3) if modifications else 0.1
        return new_cfg, diff, avg_impact
