"""Pure statistics for the experience layer (no DB, no I/O).

Confidence uses the Wilson 95% lower bound so a single success is never shown
as a 100% reliable edge; display weight combines usage volume, that
conservative confidence and a 90-day half-life time decay.  Decay only lowers
*visual* weight — raw observations are never deleted (design §5.3, decision
2026-09-11: full-history window + 90-day half-life).
"""

from __future__ import annotations

import math
from datetime import datetime

DEFAULT_Z = 1.96  # 95% two-sided
DEFAULT_HALF_LIFE_DAYS = 90.0


def wilson_lower_bound(success: int, total: int, z: float = DEFAULT_Z) -> float:
    """Wilson score lower bound in [0,1]; 0.0 when there is no sample."""
    if total <= 0:
        return 0.0
    if success < 0 or success > total:
        raise ValueError("success must be within [0, total]")
    phat = success / total
    z2 = z * z
    centre = phat + z2 / (2 * total)
    margin = z * math.sqrt((phat * (1 - phat) + z2 / (4 * total)) / total)
    lower = (centre - margin) / (1 + z2 / total)
    return max(0.0, min(1.0, lower))


def half_life_decay(age_days: float, half_life_days: float = DEFAULT_HALF_LIFE_DAYS) -> float:
    """exp(-ln2/half_life * age); 1.0 at age 0, 0.5 after one half-life."""
    if age_days <= 0:
        return 1.0
    if half_life_days <= 0:
        raise ValueError("half_life_days must be positive")
    return math.exp(-(math.log(2.0) / half_life_days) * age_days)


def display_weight(total: int, confidence: float, decay: float) -> float:
    """Visual-only weight: log volume × conservative confidence × decay."""
    if total <= 0:
        return 0.0
    return math.log1p(total) * max(0.0, min(1.0, confidence)) * max(0.0, min(1.0, decay))


def age_days(last_used_at: datetime | None, now: datetime) -> float | None:
    if last_used_at is None:
        return None
    delta = now - last_used_at
    return max(0.0, delta.total_seconds() / 86400.0)
