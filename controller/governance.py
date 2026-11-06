"""Governance Client and Lifecycle Manager (C9 & C10).

Manages approval proposals, approver working hours, fatigue limits,
auto-apply policies, approval reconciliation, and supervisor edit handling.
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any
from .client import SimClient
from .storage import Storage

logger = logging.getLogger(__name__)


def total_variation_shift(old_cfg: dict[str, Any], new_cfg: dict[str, Any]) -> float:
    """Calculate maximum total-variation distance across cohorts for agent dimensions."""
    def normalized(weights: dict[str, float]) -> dict[str, float]:
        total = sum(weights.values())
        return {key: value / total for key, value in weights.items()} if total > 0 else {}

    cohort_keys = {"default"}
    for c in old_cfg.get("cohorts", []):
        cohort_keys.add(c["id"])
    for c in new_cfg.get("cohorts", []):
        cohort_keys.add(c["id"])

    worst_tv = 0.0
    for cid in cohort_keys:
        for dim in ("welcome_message", "voice", "caller_id"):
            old_alloc = old_cfg.get("allocations", {}).get(cid, {}).get(dim)
            if not old_alloc:
                old_alloc = old_cfg.get("allocations", {}).get("default", {}).get(dim, {})
            new_alloc = new_cfg.get("allocations", {}).get(cid, {}).get(dim)
            if not new_alloc:
                new_alloc = new_cfg.get("allocations", {}).get("default", {}).get(dim, {})

            old_alloc, new_alloc = normalized(old_alloc), normalized(new_alloc)

            all_keys = set(old_alloc.keys()) | set(new_alloc.keys())
            tv = 0.5 * sum(abs(old_alloc.get(k, 0.0) - new_alloc.get(k, 0.0)) for k in all_keys)
            worst_tv = max(worst_tv, tv)

    return worst_tv


def get_changed_dimensions(old_cfg: dict[str, Any], new_cfg: dict[str, Any]) -> list[str]:
    """Identify which dimensions changed between old and new configs."""
    changed = []
    if old_cfg.get("cohorts") != new_cfg.get("cohorts"):
        changed.append("cohorts")
    if old_cfg.get("prompt_version") != new_cfg.get("prompt_version"):
        changed.append("prompt_version")
    if old_cfg.get("schedule") != new_cfg.get("schedule"):
        changed.append("schedule")

    # Check allocations
    for dim in ("welcome_message", "voice", "caller_id"):
        for cid in {"default"} | {c["id"] for c in old_cfg.get("cohorts", [])} | {c["id"] for c in new_cfg.get("cohorts", [])}:
            old_d = old_cfg.get("allocations", {}).get(cid, {}).get(dim)
            new_d = new_cfg.get("allocations", {}).get(cid, {}).get(dim)
            if old_d != new_d:
                changed.append(dim)
                break

    return sorted(list(set(changed)))


class GovernanceManager:
    """Controls approval workflows and auto-apply executions."""

    def __init__(self, client: SimClient, storage: Storage):
        self.client = client
        self.storage = storage
        # Track pending approval IDs: workflow_id -> approval_id
        self.pending_approvals: dict[str, str] = {}
        # Track submission timestamps for fatigue limits: workflow_id -> list of sim_minutes
        self.submission_times: dict[str, list[int]] = {}

    def restore_pending(self):
        self.pending_approvals = {
            row["workflow_id"]: row["approval_id"]
            for row in self.storage.get_pending_proposals()
            if row["approval_id"]
        }

    def reconcile_approvals(self, sim_minute: int, ts: str):
        """Poll and apply any approved proposals; clean up expired/rejected ones."""
        # Check active approvals from simulator
        try:
            active = self.client.list_approvals()
        except Exception:
            logger.exception("Failed to list approvals; retaining simulator time for retry")
            raise

        by_proposal = {item["proposal_id"]: item for item in active}
        for row in self.storage.get_pending_proposals():
            pid, wid = row["proposal_id"], row["workflow_id"]
            if row["status"] == "sending":
                remote = by_proposal.get(pid)
                if remote:
                    self.storage.update_proposal_status(pid, remote["status"], approval_id=remote["approval_id"])
                    if remote["status"] in ("pending", "approved"):
                        self.pending_approvals[wid] = remote["approval_id"]
                    else:
                        self.pending_approvals.pop(wid, None)
                elif row["request_body"]:
                    body = json.loads(row["request_body"])
                    if self.client.get_config(wid)["version"] != row["base_version"]:
                        self.storage.update_proposal_status(pid, "stale", sim_minute)
                    else:
                        code, response = self.client.request_approval(body)
                        if code in (200, 201):
                            self.storage.update_proposal_status(pid, "pending", approval_id=response["approval_id"])
                            self.pending_approvals[wid] = response["approval_id"]
                        elif code == 409 and response.get("error") == "duplicate_proposal_id":
                            refreshed = next((item for item in self.client.list_approvals()
                                              if item.get("proposal_id") == pid), None)
                            if refreshed:
                                self.storage.update_proposal_status(
                                    pid, refreshed["status"], approval_id=refreshed["approval_id"])
                                if refreshed["status"] in ("pending", "approved"):
                                    self.pending_approvals[wid] = refreshed["approval_id"]
                            else:
                                raise RuntimeError(f"Duplicate proposal {pid} not visible for reconciliation")
                        elif code >= 500:
                            raise RuntimeError(f"Approval retry returned HTTP {code}")
                        else:
                            status = "stale" if code == 409 and response.get("error") == "stale_base_version" else "rejected"
                            self.storage.update_proposal_status(pid, status, sim_minute, reasons=[str(response)])
            elif row["status"] == "auto_sending":
                applied = next((entry for entry in self.client.get_config_history(wid)
                                if entry.get("proposal_id") == pid), None)
                if applied:
                    self.storage.log_audit(ts, sim_minute, wid, "auto_applied", "controller_auto",
                                           from_version=row["base_version"], to_version=applied["version"],
                                           proposal_id=pid, rationale="Reconciled auto-apply after restart")
                    self.storage.update_proposal_status(pid, "auto_applied", applied_minute=sim_minute)
                elif row["request_body"] and self.client.get_config(wid)["version"] == row["base_version"]:
                    code, response = self.client.auto_apply(wid, json.loads(row["request_body"]))
                    if code in (200, 201):
                        self.storage.log_audit(ts, sim_minute, wid, "auto_applied", "controller_auto",
                                               from_version=row["base_version"], to_version=response.get("version"),
                                               proposal_id=pid, rationale="Retried durable auto-apply intent")
                        self.storage.update_proposal_status(pid, "auto_applied", applied_minute=sim_minute)
                    elif code == 409:
                        applied = next((entry for entry in self.client.get_config_history(wid)
                                        if entry.get("proposal_id") == pid), None)
                        if applied:
                            self.storage.log_audit(ts, sim_minute, wid, "auto_applied", "controller_auto",
                                                   from_version=row["base_version"], to_version=applied["version"],
                                                   proposal_id=pid, rationale="Reconciled auto-apply after conflict")
                            self.storage.update_proposal_status(pid, "auto_applied", applied_minute=sim_minute)
                        else:
                            self.storage.update_proposal_status(pid, "stale", sim_minute)
                    elif code >= 500:
                        raise RuntimeError(f"Auto-apply retry returned HTTP {code}")
                    else:
                        self.storage.update_proposal_status(pid, "auto_failed", reasons=[str(response)])
                else:
                    self.storage.update_proposal_status(pid, "stale", sim_minute)

        local = {row["approval_id"]: row for row in self.storage.get_pending_proposals()
                 if row["approval_id"]}
        for ap in active:
            aid = ap.get("approval_id")
            if aid not in local:
                continue
            wid = ap.get("workflow_id")
            status = ap.get("status")
            pid = ap.get("proposal_id")

            if status == "approved":
                # Apply immediately to avoid lapsing!
                logger.info("Applying approved proposal %s for %s (approval_id: %s)", pid, wid, aid)
                status_code, resp = self.client.apply_approval(wid, aid)
                if status_code in (200, 201):
                    logger.info("Successfully applied %s for %s! New version: %s", aid, wid, resp.get("version"))
                    self.storage.log_audit(
                        ts=ts,
                        sim_minute=sim_minute,
                        workflow_id=wid,
                        action="applied_via_approval",
                        actor="controller_approved",
                        proposal_id=pid,
                        approval_id=aid,
                        to_version=resp.get("version"),
                        rationale="Applied approved governance proposal"
                    )
                    self.storage.update_proposal_status(
                        proposal_id=pid,
                        status="applied",
                        decided_minute=sim_minute,
                        applied_minute=sim_minute,
                        approval_id=aid
                    )
                elif status_code == 409 and resp.get("error") == "stale_base_version":
                    # Stale base version (e.g. supervisor edited in the meantime)
                    logger.warning("Apply failed with stale base version for %s: %s", wid, resp)
                    self.storage.update_proposal_status(
                        proposal_id=pid,
                        status="stale",
                        decided_minute=sim_minute,
                        reasons=["stale_base_version"],
                        approval_id=aid
                    )
                elif status_code == 410:
                    self.storage.update_proposal_status(pid, "lapsed", sim_minute, reasons=["apply_window_elapsed"], approval_id=aid)
                else:
                    logger.warning("Apply deferred for %s: %s %s", aid, status_code, resp)
                    if status_code >= 500:
                        raise RuntimeError(f"Approval apply returned HTTP {status_code}")
                    continue
                self.pending_approvals.pop(wid, None)

            elif status in ("rejected", "expired", "lapsed"):
                reasons = ap.get("reasons", [])
                logger.info("Proposal %s (%s) finished with status %s: %s", pid, aid, status, reasons)
                self.storage.update_proposal_status(
                    proposal_id=pid,
                    status=status,
                    decided_minute=sim_minute,
                    reasons=reasons,
                    approval_id=aid
                )
                self.pending_approvals.pop(wid, None)

            elif status == "applied":
                self.storage.log_audit(ts, sim_minute, wid, "applied_via_approval", "controller_approved",
                                       from_version=local[aid]["base_version"], proposal_id=pid,
                                       approval_id=aid, rationale="Reconciled applied approval after restart")
                self.storage.update_proposal_status(pid, "applied", sim_minute, sim_minute, approval_id=aid)
                self.pending_approvals.pop(wid, None)
            elif status == "pending":
                self.pending_approvals[wid] = aid

    def can_propose_now(self, wf_spec: dict[str, Any], sim_minute: int, sim_hour: int) -> bool:
        """Check whether current time is within approver working hours and within fatigue limit."""
        wid = wf_spec.get("workflow_id") or wf_spec.get("id")
        gov = wf_spec.get("governance", {})
        appr_hours = gov.get("approver_hours", [10, 18])
        max_24h = gov.get("max_requests_per_24h", 3)
        if wf_spec.get("tenant_id") == "kestrel_finance":
            appr_hours = [max(11, appr_hours[0]), appr_hours[1]]
            max_24h = min(2, max_24h)

        # 1. Check approver working hours (give buffer before end of shift)
        start_h, end_h = appr_hours
        if start_h >= end_h:
            return False
        latest_h = min(end_h, max(start_h + 1, end_h - 2))
        if not (start_h <= sim_hour < latest_h):
            return False

        # 2. Check 24-hour rate limit (fatigue)
        recent = self.storage.recent_proposal_times(wid, sim_minute - 1440)
        if len(recent) >= max_24h:
            return False

        # 3. Check if an approval is already pending
        if wid in self.pending_approvals or self.storage.get_pending_proposals(wid):
            return False

        return True

    def submit_change(
        self,
        wf_spec: dict[str, Any],
        current_config_meta: dict[str, Any],
        proposed_config: dict[str, Any],
        change_summary: str,
        diff: dict[str, Any],
        rationale: str,
        impact_score: float,
        sim_minute: int,
        sim_hour: int,
        ts: str,
        evidence: dict[str, Any] | None = None,
        affected_leads: int = 1000,
    ) -> bool:
        """Attempt auto-apply if eligible, otherwise submit approval proposal during approver hours."""
        wid = wf_spec.get("workflow_id") or wf_spec.get("id")
        gov = wf_spec.get("governance", {})
        cur_cfg = current_config_meta["config"]
        cur_ver = current_config_meta["version"]

        if proposed_config == cur_cfg:
            return False
        if self.storage.get_pending_proposals(wid):
            return False

        changed_dims = get_changed_dimensions(cur_cfg, proposed_config)
        shift = total_variation_shift(cur_cfg, proposed_config)

        # Check if auto-apply is enabled and eligible
        auto_gov = gov.get("auto_apply", {})
        can_auto_apply = (
            auto_gov.get("enabled", False)
            and all(d in auto_gov.get("allowed_dims", []) for d in changed_dims)
            and shift <= auto_gov.get("max_shift", 0.0)
        )

        proposal_id = f"prop_{uuid.uuid4().hex}"

        if can_auto_apply:
            # Execute auto-apply directly
            logger.info("Executing auto-apply for %s (dims: %s, shift: %.3f)", wid, changed_dims, shift)
            payload = {
                "proposal_id": proposal_id,
                "base_version": cur_ver,
                "proposed_config": proposed_config,
                "rationale": rationale,
            }
            if evidence:
                payload["evidence"] = evidence

            self.storage.save_proposal(proposal_id, wid, cur_ver, "auto_sending", sim_minute,
                                       proposed_config, change_summary, impact_score,
                                       request_body=payload)

            status_code, resp = self.client.auto_apply(wid, payload)
            if status_code in (200, 201):
                logger.info("Auto-apply succeeded for %s! New version: %s", wid, resp.get("version"))
                self.storage.log_audit(
                    ts=ts,
                    sim_minute=sim_minute,
                    workflow_id=wid,
                    action="auto_applied",
                    actor="controller_auto",
                    from_version=cur_ver,
                    to_version=resp.get("version"),
                    proposal_id=proposal_id,
                    diff=diff,
                    rationale=rationale,
                    evidence=evidence,
                )
                self.storage.update_proposal_status(proposal_id, "auto_applied", applied_minute=sim_minute)
                return True
            else:
                if status_code >= 500:
                    raise RuntimeError(f"Auto-apply result uncertain: HTTP {status_code}")
                self.storage.update_proposal_status(proposal_id, "auto_failed", reasons=[str(resp)])
                logger.warning("Auto-apply rejected for %s: %s %s", wid, status_code, resp)
                # If auto-apply failed, fall through to proposal if permissible

        # Otherwise route through approval governance
        if not self.can_propose_now(wf_spec, sim_minute, sim_hour):
            return False

        proposal_payload = {
            "proposal_id": proposal_id,
            "workflow_id": wid,
            "base_version": cur_ver,
            "proposed_config": proposed_config,
            "change_summary": change_summary,
            "diff": diff,
            "affected_leads": affected_leads,
            "dry_run": {"method": "observed allocation comparison", "estimate_only": True,
                        "shift": round(shift, 4), "note": "No counterfactual outcomes inferred"},
            "impact_score": min(1.0, max(0.0, impact_score)),
            "rationale": rationale,
            "expires_in_hours": min(24, gov.get("proposal_ttl_hours", 24)),
        }
        if evidence:
            proposal_payload["evidence"] = evidence

        self.storage.save_proposal(proposal_id, wid, cur_ver, "sending", sim_minute,
                                   proposed_config, change_summary, impact_score,
                                   request_body=proposal_payload)

        status_code, resp = self.client.request_approval(proposal_payload)
        if status_code in (200, 201):
            aid = resp.get("approval_id")
            self.pending_approvals[wid] = aid
            self.submission_times.setdefault(wid, []).append(sim_minute)
            self.storage.save_proposal(
                proposal_id=proposal_id,
                workflow_id=wid,
                base_version=cur_ver,
                status="pending",
                submitted_minute=sim_minute,
                proposed_config=proposed_config,
                change_summary=change_summary,
                impact_score=impact_score,
                approval_id=aid,
                request_body=proposal_payload,
            )
            self.storage.log_audit(
                ts=ts,
                sim_minute=sim_minute,
                workflow_id=wid,
                action="proposal_submitted",
                actor="controller_proposer",
                from_version=cur_ver,
                proposal_id=proposal_id,
                approval_id=aid,
                diff=diff,
                rationale=rationale,
                evidence=evidence,
            )
            logger.info("Approval requested for %s: %s (aid: %s)", wid, proposal_id, aid)
            return True
        else:
            if status_code >= 500:
                raise RuntimeError(f"Approval submission result uncertain: HTTP {status_code}")
            self.storage.update_proposal_status(proposal_id, "rejected", reasons=[str(resp)])
            logger.error("Failed to request approval for %s: %s %s", wid, status_code, resp)
            return False
