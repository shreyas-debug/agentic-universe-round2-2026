"""Controller invariants that matter on retries and policy boundaries."""
import json

from controller.experiments import ExperimentManager
from controller.governance import GovernanceManager
from controller.incidents import HallucinationDetector
from controller.ingestion import IngestionEngine
from controller.storage import Storage


class ChunkClient:
    def __init__(self):
        self.lines = [
            json.dumps({"type": "call", "event_id": 1, "workflow_id": "w", "connected": True}),
            json.dumps({"type": "call", "event_id": 2, "workflow_id": "w", "connected": False}),
        ]

    def get_chunk_lines(self, _):
        yield from self.lines


def test_chunk_cursor_and_state_commit_together(tmp_path):
    store = Storage(str(tmp_path / "state.db"))
    client = ChunkClient()
    seen = []
    ing = IngestionEngine(client, store)
    ing.register_call_handler(lambda event: seen.append(event["event_id"]))
    ing.on_chunk_committed = lambda chunk, event: store.save_runtime(
        chunk, event, {"seen": seen.copy()})
    assert ing.ingest_new_chunks([{"chunk": 7}]) == 2
    restarted = IngestionEngine(client, store)
    assert restarted.last_processed_chunk == 7
    assert restarted.last_processed_event_id == 2
    assert store.get_checkpoint("runtime") == {"seen": [1, 2]}
    assert restarted.ingest_new_chunks([{"chunk": 7}]) == 0


def test_failed_chunk_does_not_advance_cursor(tmp_path):
    store = Storage(str(tmp_path / "state.db"))
    client = ChunkClient()
    client.lines.append("{invalid json")
    ing = IngestionEngine(client, store)
    try:
        ing.ingest_new_chunks([{"chunk": 2}])
    except json.JSONDecodeError:
        pass
    else:
        raise AssertionError("bad chunk was silently accepted")
    assert store.get_checkpoint("last_processed_chunk") is None


def test_pickup_primary_does_not_change_welcome_or_voice():
    wf = {"options": {"welcome_message": ["W1", "W2"], "voice": ["V1", "V2"]},
          "objective": {"primary": "pickup_rate", "guardrail": {"engagement_rate_max_drop": 0.02}}}
    cfg = {"cohorts": [], "allocations": {"default": {
        "welcome_message": {"W1": 1}, "voice": {"V1": 1}}}}
    changed, diff, score = ExperimentManager("w", wf).plan_next_allocations(cfg, [])
    assert changed == cfg and diff == {} and score == 0


def test_hallucination_requires_population():
    detector = HallucinationDetector()
    for _ in range(190):
        detector.record_call(False)
    for _ in range(9):
        detector.record_call(True)
    assert not detector.is_spiking()
    detector.record_call(True)
    assert detector.is_spiking()


def test_approval_fatigue_survives_restart(tmp_path):
    store = Storage(str(tmp_path / "state.db"))
    wf = {"workflow_id": "w", "governance": {
        "approver_hours": [10, 19], "max_requests_per_24h": 2}}
    store.save_proposal("p1", "w", 1, "rejected", 100, {}, "one", 0.2)
    store.save_proposal("p2", "w", 1, "rejected", 200, {}, "two", 0.2)
    manager = GovernanceManager(client=None, storage=store)
    assert not manager.can_propose_now(wf, 700, 11)


def test_uncertain_submission_reconciles_without_resubmitting(tmp_path):
    store = Storage(str(tmp_path / "state.db"))
    store.save_proposal("p1", "w", 1, "sending", 660, {"prompt_version": "P1"},
                        "change", 0.2, request_body={"proposal_id": "p1"})

    class Remote:
        submissions = 0
        applications = 0

        def list_approvals(self):
            return [{"approval_id": "a1", "proposal_id": "p1",
                     "workflow_id": "w", "status": "approved"}]

        def apply_approval(self, wid, aid):
            self.applications += 1
            return 200, {"version": 2}

        def request_approval(self, body):
            self.submissions += 1
            raise AssertionError("must not submit a second request")

    remote = Remote()
    manager = GovernanceManager(remote, store)
    manager.reconcile_approvals(720, "D0 12:00")
    assert remote.submissions == 0
    assert remote.applications == 1
    assert store.get_proposal("p1")["status"] == "applied"


def test_stale_approved_change_is_not_forced(tmp_path):
    store = Storage(str(tmp_path / "state.db"))
    store.save_proposal("p1", "w", 1, "pending", 660, {"prompt_version": "P1"},
                        "change", 0.2, approval_id="a1")

    class Remote:
        def list_approvals(self):
            return [{"approval_id": "a1", "proposal_id": "p1",
                     "workflow_id": "w", "status": "approved"}]

        def apply_approval(self, wid, aid):
            return 409, {"error": "stale_base_version"}

    GovernanceManager(Remote(), store).reconcile_approvals(720, "D0 12:00")
    assert store.get_proposal("p1")["status"] == "stale"
