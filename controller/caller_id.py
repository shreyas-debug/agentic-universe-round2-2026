"""Caller-ID (DID) Health & Rotation Manager.

Monitors pickup rates by caller_id using a rolling window of recent calls,
detects spam-flagging / degradation, and rotates traffic to healthy DIDs.
"""
from __future__ import annotations

import logging
from collections import deque
from typing import Any

logger = logging.getLogger(__name__)


class DidTracker:
    def __init__(self, did: str, window_size: int = 250):
        self.did = did
        self.window_size = window_size
        self.recent_calls: deque[bool] = deque(maxlen=window_size)
        self.total_dials: int = 0
        self.total_connected: int = 0

    def record_dial(self, connected: bool):
        self.total_dials += 1
        if connected:
            self.total_connected += 1
        self.recent_calls.append(connected)

    def reset_window(self):
        self.recent_calls.clear()

    @property
    def recent_dials(self) -> int:
        return len(self.recent_calls)

    @property
    def recent_pickup_rate(self) -> float:
        if not self.recent_calls:
            return 0.30
        return sum(self.recent_calls) / len(self.recent_calls)


class CallerIdManager:
    """Manages caller ID allocations and health per workflow."""

    def __init__(self, workflow_id: str, available_dids: list[str]):
        self.workflow_id = workflow_id
        self.available_dids = list(available_dids)
        self.trackers: dict[str, DidTracker] = {d: DidTracker(d) for d in available_dids}
        self.flagged_dids: set[str] = set()

    def record_call(self, did: str, connected: bool):
        if did in self.trackers:
            self.trackers[did].record_dial(connected)

    def reset_windows(self):
        """Reset rolling windows after historical bootstrap to evaluate live traffic cleanly."""
        for trk in self.trackers.values():
            trk.reset_window()

    def detect_degradation(self, min_recent_dials: int = 150) -> list[str]:
        """Detect any caller IDs that have degraded pickup rates in recent calls."""
        rates = {}
        for did, trk in self.trackers.items():
            if trk.recent_dials >= min_recent_dials:
                rates[did] = trk.recent_pickup_rate

        if len(rates) < 2:
            return []

        best_rate = max(rates.values())
        newly_flagged = []

        for did, rate in rates.items():
            if did not in self.flagged_dids:
                # If rate is significantly below best healthy rate (e.g. < 60% of healthy rate)
                if best_rate > 0.15 and rate < (best_rate * 0.55):
                    self.flagged_dids.add(did)
                    newly_flagged.append(did)
                    logger.warning(
                        "Workflow %s: DID %s flagged as degraded (recent pickup %.3f vs best %.3f)",
                        self.workflow_id, did, rate, best_rate
                    )

        return newly_flagged

    def get_healthy_allocation(self) -> dict[str, float]:
        """Compute balanced allocation among healthy DIDs.

        Distributes load equally across non-flagged DIDs to prevent saturation.
        """
        healthy = [d for d in self.available_dids if d not in self.flagged_dids]
        if not healthy:
            healthy = self.available_dids

        weight = round(1.0 / len(healthy), 4)
        alloc = {d: weight for d in healthy}
        total = sum(alloc.values())
        first = healthy[0]
        alloc[first] += round(1.0 - total, 4)
        return alloc
