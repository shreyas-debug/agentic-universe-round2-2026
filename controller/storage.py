"""Storage layer using SQLite for crash recovery, idempotency, and audit trails.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


class Storage:
    def __init__(self, db_path: str = "controller.db"):
        self.db_path = db_path
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_conn() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS checkpoints (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                sim_minute INTEGER NOT NULL,
                workflow_id TEXT NOT NULL,
                action TEXT NOT NULL,
                actor TEXT NOT NULL,
                from_version INTEGER,
                to_version INTEGER,
                proposal_id TEXT,
                approval_id TEXT,
                diff TEXT,
                rationale TEXT,
                evidence TEXT,
                outcome_after TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_audit_wf_ts ON audit_log (workflow_id, sim_minute);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_audit_proposal_action
                ON audit_log (proposal_id, action) WHERE proposal_id IS NOT NULL;

            CREATE TABLE IF NOT EXISTS proposals (
                proposal_id TEXT PRIMARY KEY,
                workflow_id TEXT NOT NULL,
                approval_id TEXT,
                base_version INTEGER NOT NULL,
                status TEXT NOT NULL,
                submitted_minute INTEGER NOT NULL,
                decided_minute INTEGER,
                applied_minute INTEGER,
                proposed_config TEXT NOT NULL,
                change_summary TEXT NOT NULL,
                impact_score REAL NOT NULL,
                reasons TEXT,
                request_body TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_proposals_wf_status ON proposals (workflow_id, status);

            CREATE TABLE IF NOT EXISTS incident_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sim_minute INTEGER NOT NULL,
                ts TEXT NOT NULL,
                workflow_id TEXT NOT NULL,
                incident_type TEXT NOT NULL,
                detail TEXT NOT NULL,
                resolved_minute INTEGER,
                resolution TEXT
            );
            CREATE TABLE IF NOT EXISTS loop_metrics (
                sim_minute INTEGER PRIMARY KEY,
                events_ingested INTEGER NOT NULL,
                ingest_seconds REAL NOT NULL,
                tick_seconds REAL NOT NULL,
                credits_remaining REAL NOT NULL
            );
            """)
            cols = {row[1] for row in conn.execute("PRAGMA table_info(proposals)")}
            if "request_body" not in cols:
                conn.execute("ALTER TABLE proposals ADD COLUMN request_body TEXT")

    def set_checkpoint(self, key: str, value: Any):
        with self._get_conn() as conn:
            val_str = json.dumps(value) if not isinstance(value, str) else value
            conn.execute(
                "INSERT INTO checkpoints (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, val_str)
            )

    def get_checkpoint(self, key: str, default: Any = None) -> Any:
        with self._get_conn() as conn:
            row = conn.execute("SELECT value FROM checkpoints WHERE key = ?", (key,)).fetchone()
            if row is None:
                return default
            try:
                return json.loads(row["value"])
            except Exception:
                return row["value"]

    def save_runtime(self, chunk: int, event_id: int, runtime: dict[str, Any]):
        """Commit the processed chunk and its derived state together."""
        values = {
            "last_processed_chunk": chunk,
            "last_processed_event_id": event_id,
            "runtime": runtime,
        }
        with self._get_conn() as conn:
            for key, value in values.items():
                conn.execute(
                    "INSERT INTO checkpoints(key,value) VALUES (?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, json.dumps(value)),
                )

    def reset(self):
        with self._get_conn() as conn:
            for table in ("checkpoints", "audit_log", "proposals", "incident_log", "loop_metrics"):
                conn.execute(f"DELETE FROM {table}")

    def record_metrics(self, sim_minute: int, events: int, ingest_seconds: float,
                       tick_seconds: float, credits_remaining: float):
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO loop_metrics VALUES (?,?,?,?,?) "
                "ON CONFLICT(sim_minute) DO UPDATE SET events_ingested=excluded.events_ingested, "
                "ingest_seconds=excluded.ingest_seconds, tick_seconds=excluded.tick_seconds, "
                "credits_remaining=excluded.credits_remaining",
                (sim_minute, events, ingest_seconds, tick_seconds, credits_remaining),
            )

    def log_audit(
        self,
        ts: str,
        sim_minute: int,
        workflow_id: str,
        action: str,
        actor: str,
        from_version: int | None = None,
        to_version: int | None = None,
        proposal_id: str | None = None,
        approval_id: str | None = None,
        diff: dict[str, Any] | None = None,
        rationale: str | None = None,
        evidence: dict[str, Any] | None = None,
        outcome_after: dict[str, Any] | None = None,
    ):
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO audit_log (
                    ts, sim_minute, workflow_id, action, actor, from_version, to_version,
                    proposal_id, approval_id, diff, rationale, evidence, outcome_after
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ts,
                    sim_minute,
                    workflow_id,
                    action,
                    actor,
                    from_version,
                    to_version,
                    proposal_id,
                    approval_id,
                    json.dumps(diff) if diff else None,
                    rationale,
                    json.dumps(evidence) if evidence else None,
                    json.dumps(outcome_after) if outcome_after else None,
                ),
            )

    def save_proposal(
        self,
        proposal_id: str,
        workflow_id: str,
        base_version: int,
        status: str,
        submitted_minute: int,
        proposed_config: dict[str, Any],
        change_summary: str,
        impact_score: float,
        approval_id: str | None = None,
        request_body: dict[str, Any] | None = None,
    ):
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO proposals (
                    proposal_id, workflow_id, approval_id, base_version, status,
                    submitted_minute, proposed_config, change_summary, impact_score, request_body
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(proposal_id) DO UPDATE SET
                    approval_id=coalesce(excluded.approval_id, proposals.approval_id),
                    status=excluded.status,
                    request_body=coalesce(excluded.request_body, proposals.request_body)
                """,
                (
                    proposal_id,
                    workflow_id,
                    approval_id,
                    base_version,
                    status,
                    submitted_minute,
                    json.dumps(proposed_config),
                    change_summary,
                    impact_score,
                    json.dumps(request_body) if request_body else None,
                ),
            )

    def update_proposal_status(
        self,
        proposal_id: str,
        status: str,
        decided_minute: int | None = None,
        applied_minute: int | None = None,
        reasons: list[str] | None = None,
        approval_id: str | None = None,
    ):
        with self._get_conn() as conn:
            conn.execute(
                """
                UPDATE proposals
                SET status = ?,
                    decided_minute = coalesce(?, decided_minute),
                    applied_minute = coalesce(?, applied_minute),
                    reasons = coalesce(?, reasons),
                    approval_id = coalesce(?, approval_id)
                WHERE proposal_id = ?
                """,
                (
                    status,
                    decided_minute,
                    applied_minute,
                    json.dumps(reasons) if reasons else None,
                    approval_id,
                    proposal_id,
                ),
            )

    def get_pending_proposals(self, workflow_id: str | None = None) -> list[dict[str, Any]]:
        with self._get_conn() as conn:
            query = "SELECT * FROM proposals WHERE status IN ('sending', 'auto_sending', 'pending', 'approved')"
            params: list[Any] = []
            if workflow_id:
                query += " AND workflow_id = ?"
                params.append(workflow_id)
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]

    def recent_proposal_times(self, workflow_id: str, since_minute: int) -> list[int]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT submitted_minute FROM proposals WHERE workflow_id=? AND submitted_minute>?",
                (workflow_id, since_minute),
            ).fetchall()
            return [row[0] for row in rows]

    def get_proposal(self, proposal_id: str) -> dict[str, Any] | None:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM proposals WHERE proposal_id=?", (proposal_id,)).fetchone()
            return dict(row) if row else None

    def query_audit(self, workflow_id: str, at_minute: int | None = None) -> list[dict[str, Any]]:
        with self._get_conn() as conn:
            if at_minute is not None:
                before = conn.execute(
                    "SELECT * FROM audit_log WHERE workflow_id=? AND sim_minute<=? "
                    "ORDER BY sim_minute DESC, id DESC LIMIT 5",
                    (workflow_id, at_minute),
                ).fetchall()
                after = conn.execute(
                    "SELECT * FROM audit_log WHERE workflow_id=? AND sim_minute>? "
                    "ORDER BY sim_minute ASC, id ASC LIMIT 10",
                    (workflow_id, at_minute),
                ).fetchall()
                return [dict(r) for r in reversed(before)] + [dict(r) for r in after]
            else:
                query = """
                SELECT * FROM audit_log
                WHERE workflow_id = ?
                ORDER BY sim_minute DESC
                LIMIT 20
                """
                rows = conn.execute(query, (workflow_id,)).fetchall()
            return [dict(r) for r in rows]
