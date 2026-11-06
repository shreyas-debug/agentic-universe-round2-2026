"""Main Controller Loop.

Connects the ingestion engine, experimentation bandits, caller-ID rotation,
hallucination incident responder, and governance client into a continuous loop.
"""
from __future__ import annotations

import copy
import logging
import time
from collections import deque
from typing import Any
from .caller_id import CallerIdManager
from .client import SimClient
from .cohorts import generate_standard_cohorts
from .experiments import ExperimentManager
from .governance import GovernanceManager
from .incidents import IncidentManager
from .ingestion import IngestionEngine
from .storage import Storage

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("controller.loop")


class OutcomeController:
    """The master outcome-first agent loop controller."""

    def __init__(self, sim_url: str = "http://127.0.0.1:8700", db_path: str = "controller.db"):
        self.client = SimClient(base_url=sim_url)
        self.storage = Storage(db_path=db_path)
        self.ingestion = IngestionEngine(client=self.client, storage=self.storage)
        self.governance = GovernanceManager(client=self.client, storage=self.storage)
        self.incident_mgr = IncidentManager(client=self.client, storage=self.storage, governance=self.governance)

        # State per workflow
        self.wfs: dict[str, dict[str, Any]] = {}
        self.did_managers: dict[str, CallerIdManager] = {}
        self.exp_managers: dict[str, ExperimentManager] = {}
        self.cohort_defs: dict[str, list[dict[str, Any]]] = {}

        self._setup_event_handlers()
        self.ingestion.on_chunk_committed = self._commit_chunk

    def _runtime_state(self) -> dict[str, Any]:
        return {
            "experiments": {wid: {
                cid: {dim: dict(arms) for dim, arms in dimensions.items()}
                for cid, dimensions in mgr.stats.items()
            } for wid, mgr in self.exp_managers.items()},
            "active_winners": {wid: [
                {"cohort": cid, "dimension": dim, "variant": val, "peak": peak}
                for (cid, dim), (val, peak) in mgr.active_winners.items()
            ] for wid, mgr in self.exp_managers.items()},
            "dids": {wid: {
                "flagged": sorted(mgr.flagged_dids),
                "trackers": {did: {"recent": list(t.recent_calls), "dials": t.total_dials,
                                   "connected": t.total_connected}
                             for did, t in mgr.trackers.items()},
            } for wid, mgr in self.did_managers.items()},
            "incidents": {wid: {"recent": list(det.recent_calls),
                                "proposal": det.incident_pending_proposal_id,
                                "last_attempt": det.last_attempt_minute}
                          for wid, det in self.incident_mgr.detectors.items()},
            "initial_prompt": self.incident_mgr.initial_prompt,
            "supervisor_prompt_edits": sorted(self.incident_mgr.supervisor_prompt_edits),
        }

    def _restore_runtime(self, state: dict[str, Any]):
        for wid, cohorts in state.get("experiments", {}).items():
            if wid not in self.exp_managers:
                continue
            for cid, dimensions in cohorts.items():
                for dim, arms in dimensions.items():
                    self.exp_managers[wid].stats[cid][dim].update(arms)
        for wid, winners in state.get("active_winners", {}).items():
            if wid in self.exp_managers:
                self.exp_managers[wid].active_winners = {
                    (item["cohort"], item["dimension"]): (item["variant"], item["peak"])
                    for item in winners
                }
        for wid, data in state.get("dids", {}).items():
            if wid not in self.did_managers:
                continue
            mgr = self.did_managers[wid]
            mgr.flagged_dids = set(data.get("flagged", []))
            for did, values in data.get("trackers", {}).items():
                if did in mgr.trackers:
                    trk = mgr.trackers[did]
                    trk.recent_calls.extend(values.get("recent", []))
                    trk.total_dials = values.get("dials", 0)
                    trk.total_connected = values.get("connected", 0)
        for wid, data in state.get("incidents", {}).items():
            det = self.incident_mgr.get_or_create_detector(wid)
            det.recent_calls = deque(data.get("recent", []), maxlen=300)
            det.incident_pending_proposal_id = data.get("proposal")
            det.last_attempt_minute = data.get("last_attempt", -1440)
        self.incident_mgr.initial_prompt = state.get("initial_prompt", {})
        self.incident_mgr.supervisor_prompt_edits = set(state.get("supervisor_prompt_edits", []))

    def _commit_chunk(self, chunk: int, event_id: int):
        self.storage.save_runtime(chunk, event_id, self._runtime_state())

    def _setup_event_handlers(self):
        self.ingestion.register_call_handler(self._on_call_event)
        self.ingestion.register_config_changed_handler(self._on_config_changed_event)
        self.ingestion.register_approval_decided_handler(self._on_approval_decided_event)

    def _on_call_event(self, ev: dict[str, Any]):
        wid = ev.get("workflow_id")
        if not wid or wid not in self.wfs:
            return

        connected = bool(ev.get("connected", False))
        engaged = bool(ev.get("engaged", False))
        variant = ev.get("variant", {})
        qa = ev.get("qa")
        cohort_id = ev.get("cohort_id", "default")
        did = variant.get("caller_id")

        # 1. Update Caller ID stats
        if did and wid in self.did_managers:
            self.did_managers[wid].record_call(did, connected)

        # 2. Update Experimentation stats
        if wid in self.exp_managers:
            self.exp_managers[wid].record_call(cohort_id, variant, connected, engaged)

        # 3. Check QA for hallucination spikes
        self.incident_mgr.record_call(wid, qa)

    def _on_config_changed_event(self, ev: dict[str, Any]):
        wid = ev.get("workflow_id")
        actor = ev.get("actor")
        new_version = ev.get("version")
        ts = ev.get("ts", "")

        if actor == "supervisor":
            logger.warning("Workflow %s: Supervisor edit detected at %s! New version: %s", wid, ts, new_version)
            # Invalidate any pending approval for this workflow
            if wid in self.governance.pending_approvals:
                stale_aid = self.governance.pending_approvals.pop(wid)
                logger.info("Invalidating pending approval %s for %s due to supervisor edit", stale_aid, wid)

            self.storage.log_audit(
                ts=ts,
                sim_minute=self.storage.get_checkpoint("sim_minute", 0),
                workflow_id=wid,
                action="supervisor_edit_detected",
                actor="supervisor",
                to_version=new_version,
                rationale="Supervisor modified config directly",
            )
            self.incident_mgr.mark_supervisor_edit(wid, ev.get("changed_dims", []))

    def _on_approval_decided_event(self, ev: dict[str, Any]):
        wid = ev.get("workflow_id")
        aid = ev.get("approval_id")
        status = ev.get("status")
        logger.info("Approval decided event: %s on %s -> %s", aid, wid, status)

    def initialize(self):
        """Fetch all workflows, generate canonical cohorts, and bootstrap history."""
        logger.info("Initializing OutcomeController against simulator...")
        self.client.wait_for_ready(timeout_s=600)
        state = self.client.get_state()
        previous_scenario = self.storage.get_checkpoint("scenario")
        previous_minute = self.storage.get_checkpoint("sim_minute", -1)
        if (previous_scenario and previous_scenario != state["scenario"]) or previous_minute > state["sim_minute"]:
            logger.info("New simulator run detected; clearing prior controller state")
            self.storage.reset()
            self.ingestion.last_processed_chunk = -1
            self.ingestion.last_processed_event_id = -1
        self.storage.set_checkpoint("scenario", state["scenario"])
        workflows_list = self.client.get_workflows()
        for wf in workflows_list:
            wid = wf.get("workflow_id") or wf.get("id")
            self.wfs[wid] = wf
            opts = wf.get("options", {})
            self.did_managers[wid] = CallerIdManager(wid, opts.get("caller_id", []))
            self.exp_managers[wid] = ExperimentManager(wid, wf)
            self.cohort_defs[wid] = generate_standard_cohorts(wid)

        runtime = self.storage.get_checkpoint("runtime")
        if runtime is not None:
            self._restore_runtime(runtime)
        else:
            self.incident_mgr.initial_prompt = {
                wid: self.client.get_config(wid)["config"]["prompt_version"] for wid in self.wfs
            }
            self.ingestion.bootstrap_history()
            for mgr in self.did_managers.values():
                mgr.reset_windows()
            for det in self.incident_mgr.detectors.values():
                det.recent_calls.clear()
            self.storage.save_runtime(-1, -1, self._runtime_state())
        self.governance.restore_pending()

    def run(self, step_minutes: int = 60):
        """Execute the continuous controller loop until simulation finishes."""
        self.initialize()
        logger.info("Starting simulation loop (step=%d minutes)...", step_minutes)

        loop_start_time = time.time()
        sim_day_logged = -1

        while True:
            # Drain backlog first: a previous process may have crashed after
            # advancing the world but before ingesting its returned chunks.
            backlog = self.client.list_chunks(kind="events", after=self.ingestion.last_processed_chunk)
            self.ingestion.ingest_new_chunks(backlog)
            state_before = self.client.get_state()
            if state_before.get("finished"):
                logger.info("Simulation completed!")
                break
            # 1. Advance simulation time
            tick_started = time.perf_counter()
            adv_res = self.client.advance(minutes=step_minutes)

            sim_state = self.client.get_state()
            sim_minute = sim_state["sim_minute"]
            sim_ts = sim_state["ts"]
            credits_remaining = sim_state.get("credits_remaining", 0.0)
            self.storage.set_checkpoint("sim_minute", sim_minute)
            self.storage.set_checkpoint("sim_ts", sim_ts)

            current_day = sim_minute // 1440
            current_hour = (sim_minute % 1440) // 60

            if current_day != sim_day_logged:
                logger.info("=== Sim Day %d | Time: %s | Credits: %.1f ===", current_day, sim_ts, credits_remaining)
                sim_day_logged = current_day

            # 2. Ingest real-time events
            new_chunks = adv_res.get("new_chunks", [])
            ingest_started = time.perf_counter()
            ingested_count = self.ingestion.ingest_new_chunks(new_chunks)
            ingest_seconds = time.perf_counter() - ingest_started

            # 3. Reconcile pending approvals (apply approved, clean up expired)
            self.governance.reconcile_approvals(sim_minute, sim_ts)

            # 4. Check for hallucination incidents on each workflow
            for wid, wf_spec in self.wfs.items():
                cur_cfg_meta = self.client.get_config(wid)
                self.incident_mgr.check_and_remediate(
                    workflow_id=wid,
                    wf_spec=wf_spec,
                    current_config_meta=cur_cfg_meta,
                    sim_minute=sim_minute,
                    ts=sim_ts,
                    credits_remaining=credits_remaining,
                )

            # 5. Check Caller ID health (rotate degraded DIDs immediately via auto-apply or proposal)
            for wid, wf_spec in self.wfs.items():
                did_mgr = self.did_managers[wid]
                newly_flagged = did_mgr.detect_degradation()
                if did_mgr.flagged_dids:
                    if newly_flagged:
                        logger.warning("Workflow %s: Detected newly degraded DIDs: %s", wid, newly_flagged)
                    cur_cfg_meta = self.client.get_config(wid)
                    cur_cfg = cur_cfg_meta["config"]
                    new_alloc = did_mgr.get_healthy_allocation()

                    proposed_cfg = copy.deepcopy(cur_cfg)
                    proposed_cfg["allocations"]["default"]["caller_id"] = new_alloc
                    for c in proposed_cfg.get("cohorts", []):
                        cid = c["id"]
                        if cid in proposed_cfg["allocations"] and "caller_id" in proposed_cfg["allocations"][cid]:
                            proposed_cfg["allocations"][cid]["caller_id"] = new_alloc

                    self.governance.submit_change(
                        wf_spec=wf_spec,
                        current_config_meta=cur_cfg_meta,
                        proposed_config=proposed_cfg,
                        change_summary=f"Rotate away from degraded caller IDs: {', '.join(sorted(did_mgr.flagged_dids))}",
                        diff={"allocations.default.caller_id": new_alloc},
                        rationale="Significant pickup rate degradation detected on flagged DIDs",
                        impact_score=0.75,
                        sim_minute=sim_minute,
                        sim_hour=current_hour,
                        ts=sim_ts,
                        affected_leads=3000,
                    )

            # 6. At most one experiment proposal per workflow per day.
            if sim_minute % 60 == 0:
                for wid, wf_spec in self.wfs.items():
                    decision_hour = max(11, wf_spec.get("governance", {}).get("approver_hours", [10, 18])[0])
                    if current_hour != decision_hour:
                        continue
                    # Only propose if we don't already have a pending proposal for this workflow
                    if wid in self.governance.pending_approvals:
                        continue

                    cur_cfg_meta = self.client.get_config(wid)
                    cur_cfg = cur_cfg_meta["config"]

                    # Evaluate Multi-Armed Bandit variant allocations
                    exp_mgr = self.exp_managers[wid]
                    active_cohorts = self.cohort_defs.get(wid, [])
                    optimized_cfg, diff, impact = exp_mgr.plan_next_allocations(cur_cfg, active_cohorts)

                    if optimized_cfg != cur_cfg:
                        self.governance.submit_change(
                            wf_spec=wf_spec,
                            current_config_meta=cur_cfg_meta,
                            proposed_config=optimized_cfg,
                            change_summary="Test or promote measured welcome and voice variants",
                            diff=diff,
                            rationale="Allocation change based on observed variant samples; pickup guardrail checked separately",
                            impact_score=impact,
                            sim_minute=sim_minute,
                            sim_hour=current_hour,
                            ts=sim_ts,
                            affected_leads=wf_spec.get("daily_batch", {}).get("size", 0),
                        )

            self.storage.record_metrics(sim_minute, ingested_count, ingest_seconds,
                                        time.perf_counter() - tick_started, credits_remaining)
            if adv_res.get("finished", False):
                logger.info("Simulation completed!")
                break

        total_runtime = time.time() - loop_start_time
        logger.info("Loop finished in %.2f seconds wall-clock time.", total_runtime)
