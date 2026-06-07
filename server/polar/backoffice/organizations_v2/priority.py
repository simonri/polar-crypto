"""Priority score based on age in status."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from polar.models import Organization

# Aging: 2.5 pts per day, capped at 50.
AGING_DAILY_PTS = 2.5
AGING_MAX_PTS = 50.0


@dataclass
class Signals:
    aging_pts: float = 0.0

    @property
    def priority(self) -> float:
        return self.aging_pts


def _days_between(later: datetime, earlier: datetime) -> float:
    return max(0.0, (later - earlier).total_seconds() / 86400.0)


def _days_in_status(org: Organization, now: datetime) -> float:
    return _days_between(now, org.status_updated_at or org.created_at)


def _aging_component(org: Organization, now: datetime) -> float:
    return min(_days_in_status(org, now) * AGING_DAILY_PTS, AGING_MAX_PTS)


def compute(
    org: Organization,
    *,
    now: datetime | None = None,
) -> Signals:
    """Compute the priority breakdown for an org."""
    if now is None:
        now = datetime.now(UTC)

    return Signals(
        aging_pts=_aging_component(org, now),
    )
