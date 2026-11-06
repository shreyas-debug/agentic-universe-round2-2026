"""Statistical computation and experimentation metrics.

Provides proportion hypothesis testing, confidence intervals, Thompson sampling/UCB,
and decay detection for online variants.
"""
from __future__ import annotations

import math
from typing import Any


def proportion_ci(successes: int, trials: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion."""
    if trials <= 0:
        return 0.0, 1.0
    p = successes / trials
    denom = 1 + (z ** 2) / trials
    centre = (p + (z ** 2) / (2 * trials)) / denom
    spread = (z * math.sqrt((p * (1 - p) + (z ** 2) / (4 * trials)) / trials)) / denom
    return max(0.0, centre - spread), min(1.0, centre + spread)


def compare_proportions(
    s1: int, n1: int, s2: int, n2: int
) -> tuple[float, float, bool]:
    """Two-proportion Z-test comparing variant 2 (candidate) vs variant 1 (baseline).

    Returns:
        (delta, z_score, is_significant_positive)
    """
    if n1 <= 10 or n2 <= 10:
        return 0.0, 0.0, False
    p1 = s1 / n1
    p2 = s2 / n2
    delta = p2 - p1

    p_pooled = (s1 + s2) / (n1 + n2)
    se = math.sqrt(p_pooled * (1 - p_pooled) * (1 / n1 + 1 / n2))
    if se <= 1e-9:
        return delta, 0.0, False
    z = delta / se

    # 95% one-tailed significance: z > 1.645 (or 95% two-tailed: z > 1.96)
    is_significant = z > 1.96
    return delta, z, is_significant


class BetaArm:
    """Beta-binomial posterior tracker for Thompson Sampling."""
    def __init__(self, alpha: float = 1.0, beta: float = 1.0):
        self.alpha = alpha
        self.beta = beta
        self.trials = 0
        self.successes = 0

    def update(self, success: bool):
        self.trials += 1
        if success:
            self.successes += 1
            self.alpha += 1.0
        else:
            self.beta += 1.0

    @property
    def mean(self) -> float:
        return self.alpha / (self.alpha + self.beta)


class VariantTracker:
    """Tracks performance of a variant (welcome_message, voice, caller_id)."""
    def __init__(self, name: str):
        self.name = name
        self.dials: int = 0
        self.connected: int = 0
        self.engaged: int = 0
        self.hallucinations: int = 0
        # Rolling recent window for decay detection (last N days / calls)
        self.recent_connected: int = 0
        self.recent_engaged: int = 0

    def record_call(self, connected: bool, engaged: bool, halluc: bool = False):
        self.dials += 1
        if connected:
            self.connected += 1
            self.recent_connected += 1
            if engaged:
                self.engaged += 1
                self.recent_engaged += 1
            if halluc:
                self.hallucinations += 1

    def reset_recent(self):
        self.recent_connected = 0
        self.recent_engaged = 0

    @property
    def pickup_rate(self) -> float:
        return self.connected / max(1, self.dials)

    @property
    def engagement_rate(self) -> float:
        return self.engaged / max(1, self.connected)

    @property
    def recent_engagement_rate(self) -> float:
        return self.recent_engaged / max(1, self.recent_connected)


def detect_decay(tracker: VariantTracker, historical_rate: float, threshold_drop: float = 0.08, min_samples: int = 100) -> bool:
    """Detect if an active winner's recent engagement rate has decayed significantly."""
    if tracker.recent_connected < min_samples:
        return False
    recent = tracker.recent_engagement_rate
    # Decay detected if recent rate is down by more than threshold_drop relative to historical
    return (historical_rate - recent) > threshold_drop
