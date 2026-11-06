"""Population-level hallucination detection and governed remediation."""
from __future__ import annotations

from collections import deque
import logging
from typing import Any

from .client import SimClient
from .governance import GovernanceManager
from .storage import Storage

logger = logging.getLogger(__name__)


class HallucinationDetector:
    def __init__(self):
        self.recent_calls: deque[bool] = deque(maxlen=300)
        self.incident_pending_proposal_id: str | None = None
        self.last_attempt_minute = -1440

    def record_call(self, flag: bool):
        self.recent_calls.append(flag)

    @property
    def current_rate(self) -> float:
        return sum(self.recent_calls) / len(self.recent_calls) if self.recent_calls else 0.0

    def is_spiking(self) -> bool:
        return len(self.recent_calls) >= 200 and sum(self.recent_calls) >= 10 and self.current_rate >= 0.04


class IncidentManager:
    def __init__(self, client: SimClient, storage: Storage, governance: GovernanceManager):
        self.client = client
        self.storage = storage
        self.governance = governance
        self.detectors: dict[str, HallucinationDetector] = {}
        self.initial_prompt: dict[str, str] = {}
        self.supervisor_prompt_edits: set[str] = set()

    def get_or_create_detector(self, workflow_id: str) -> HallucinationDetector:
        return self.detectors.setdefault(workflow_id, HallucinationDetector())

    def record_call(self, workflow_id: str, qa: dict[str, Any] | None):
        if qa is not None:
            self.get_or_create_detector(workflow_id).record_call(bool(qa.get("hallucination_flag")))

    def mark_supervisor_edit(self, workflow_id: str, changed_dims: list[str]):
        if "prompt_version" in changed_dims:
            self.supervisor_prompt_edits.add(workflow_id)
            self.get_or_create_detector(workflow_id).recent_calls.clear()

    def check_and_remediate(
        self, workflow_id: str, wf_spec: dict[str, Any],
        current_config_meta: dict[str, Any], sim_minute: int,
        ts: str, credits_remaining: float,
    ) -> None:
        det = self.get_or_create_detector(workflow_id)
        if det.incident_pending_proposal_id:
            proposal = self.storage.get_proposal(det.incident_pending_proposal_id)
            status = proposal["status"] if proposal else None
            if status in ("pending", "approved"):
                return
            if status == "applied":
                det.recent_calls.clear()
            det.incident_pending_proposal_id = None
        if not det.is_spiking() or sim_minute - det.last_attempt_minute < 1440:
            return
        if not self.governance.can_propose_now(
            wf_spec, sim_minute, (sim_minute % 1440) // 60
        ):
            return
        if credits_remaining < 12:
            logger.warning("%s: incident needs credit reserve (remaining=%s)", workflow_id, credits_remaining)
            return

        det.last_attempt_minute = sim_minute
        try:
            suggestions = self.client.suggest_fixes(
                workflow_id, f"Hallucination flags {sum(det.recent_calls)}/{len(det.recent_calls)} connected calls"
            )["suggestions"]
            current_prompt = current_config_meta["config"]["prompt_version"]
            candidates = [s for s in suggestions if s["prompt_version"] != current_prompt]
            baseline = self.initial_prompt.get(workflow_id)
            if workflow_id in self.supervisor_prompt_edits and baseline and baseline != current_prompt:
                candidates.insert(0, {"prompt_version": baseline, "summary": "Previously used production prompt"})
            seen: set[str] = set()
            candidates = [c for c in candidates
                          if not (c["prompt_version"] in seen or seen.add(c["prompt_version"]))]
            threshold = max(0.985, wf_spec.get("governance", {}).get("min_validation_pass_rate", 0.0))
            chosen = None
            for candidate in candidates[:2]:
                run = self.client.run_validation(workflow_id, candidate["prompt_version"], n=400)
                if run["pass_rate"] >= threshold:
                    chosen = (candidate, run)
                    break
            if not chosen:
                self.storage.log_audit(ts, sim_minute, workflow_id, "incident_no_safe_candidate",
                                       "controller", rationale="No tested prompt passed safety threshold",
                                       evidence={"flagged": sum(det.recent_calls), "sample": len(det.recent_calls)})
                return
            candidate, run = chosen
            proposed = dict(current_config_meta["config"])
            proposed["prompt_version"] = candidate["prompt_version"]
            rationale = (f"{sum(det.recent_calls)}/{len(det.recent_calls)} recent connected calls flagged; "
                         f"synthetic safety pass {run['pass_rate']:.3f}. Engagement impact unknown.")
            submitted = self.governance.submit_change(
                wf_spec=wf_spec, current_config_meta=current_config_meta,
                proposed_config=proposed,
                change_summary=f"Review hallucination remediation: {candidate['prompt_version']}",
                diff={"prompt_version": {"from": current_prompt, "to": candidate["prompt_version"]}},
                rationale=rationale, impact_score=min(1.0, det.current_rate / 0.1),
                sim_minute=sim_minute, sim_hour=(sim_minute % 1440) // 60, ts=ts,
                evidence={"validation_run_id": run["validation_run_id"]},
                affected_leads=wf_spec.get("daily_batch", {}).get("size", 0),
            )
            if submitted:
                pending = self.storage.get_pending_proposals(workflow_id)
                if pending:
                    det.incident_pending_proposal_id = pending[0]["proposal_id"]
                self.storage.log_audit(ts, sim_minute, workflow_id, "incident_proposed", "controller",
                                       rationale=rationale,
                                       evidence={"validation_run_id": run["validation_run_id"]})
        except Exception:
            logger.exception("Incident remediation failed for %s", workflow_id)
