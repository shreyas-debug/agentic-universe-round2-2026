"""Event Ingestion and History Bootstrapping Engine (C1 & C3).

Streams events from chunked NDJSON logs, joins lead features, aggregates
performance metrics, and detects supervisor changes with high throughput.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable
from .client import SimClient
from .storage import Storage

logger = logging.getLogger(__name__)


class IngestionEngine:
    """Fast, backlog-safe event ingestion engine."""

    def __init__(self, client: SimClient, storage: Storage):
        self.client = client
        self.storage = storage
        self.last_processed_chunk = storage.get_checkpoint("last_processed_chunk", -1)
        self.last_processed_event_id = storage.get_checkpoint("last_processed_event_id", -1)
        # Callback hooks for event dispatching
        self.call_handlers: list[Callable[[dict[str, Any]], None]] = []
        self.config_changed_handlers: list[Callable[[dict[str, Any]], None]] = []
        self.approval_decided_handlers: list[Callable[[dict[str, Any]], None]] = []
        self.on_chunk_committed: Callable[[int, int], None] | None = None

    def register_call_handler(self, fn: Callable[[dict[str, Any]], None]):
        self.call_handlers.append(fn)

    def register_config_changed_handler(self, fn: Callable[[dict[str, Any]], None]):
        self.config_changed_handlers.append(fn)

    def register_approval_decided_handler(self, fn: Callable[[dict[str, Any]], None]):
        self.approval_decided_handlers.append(fn)

    def bootstrap_history(self):
        """Ingest all historical chunks prior to D0 to seed statistics."""
        logger.info("Bootstrapping historical chunks...")
        try:
            history_chunks = self.client.list_chunks(kind="history", after=-1)
        except Exception as e:
            logger.error("Failed to list history chunks: %s", e)
            return

        total_history_events = 0
        # Two days is enough for a prior without loading the entire 14-day
        # history into the controller on the full-scale scenario.
        for ch in history_chunks:
            if ch.get("until_minute", 0) < -2880:
                continue
            chunk_num = ch["chunk"]
            lines = self.client.get_chunk_lines(chunk_num)
            for line in lines:
                ev = json.loads(line)
                self._process_single_event(ev)
                total_history_events += 1

        logger.info("Bootstrapped %d events across %d history chunks", total_history_events, len(history_chunks))

    def ingest_new_chunks(self, new_chunks: list[dict[str, Any]]) -> int:
        """Process real-time chunks returned from /sim/advance."""
        count = 0
        for ch in sorted(new_chunks, key=lambda item: item["chunk"]):
            chunk_num = ch["chunk"]
            if chunk_num <= self.last_processed_chunk:
                continue

            lines = self.client.get_chunk_lines(chunk_num)
            last_event_id = self.last_processed_event_id
            for line in lines:
                ev = json.loads(line)
                ev_id = ev.get("event_id", -1)
                if ev_id <= self.last_processed_event_id and ev_id != -1:
                    continue
                self._process_single_event(ev)
                last_event_id = max(last_event_id, ev_id)
                count += 1

            # Derived state and cursor must be one durable checkpoint. A crash
            # before this point replays the entire chunk on restored state.
            if self.on_chunk_committed:
                self.on_chunk_committed(chunk_num, last_event_id)
            else:
                self.storage.save_runtime(chunk_num, last_event_id, {})
            self.last_processed_chunk = chunk_num
            self.last_processed_event_id = last_event_id
        return count

    def _process_single_event(self, ev: dict[str, Any]):
        ev_type = ev.get("type")

        if ev_type == "call":
            for handler in self.call_handlers:
                handler(ev)

        elif ev_type == "config_changed":
            for handler in self.config_changed_handlers:
                handler(ev)

        elif ev_type == "approval_decided":
            for handler in self.approval_decided_handlers:
                handler(ev)
