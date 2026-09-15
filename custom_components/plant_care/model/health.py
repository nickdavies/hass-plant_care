"""Faults that would otherwise read as a healthy plant.

Every check here exists because its failure mode is silent. A flat battery, a
probe knocked out of the soil, or water channelling down the side of a dried-out
rootball all look exactly like stable healthy soil to anything watching a
threshold, and all of them end with a dead plant and no alert.

They are reported as items in the same outstanding feed as everything else,
rather than as notifications. A fault and an overdue feeding both mean "this
plant needs you"; routing them through different machinery would mean two places
to look.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class IssueKind(Enum):
    """What is wrong. Distinct from care items, which are schedules."""

    BATTERY_LOW = "battery_low"
    PROBE_SILENT = "probe_silent"
    PROBE_STUCK = "probe_stuck"
    WATERLOGGED = "waterlogged"
    WATERING_SHORTFALL = "watering_shortfall"


@dataclass(frozen=True)
class HealthIssue:
    """One fault, ready to become a feed item.

    `detail` carries the remedy, not just the symptom — these reach a human who
    has to decide what to do about a number, and "soil has sat above field
    capacity for 27 hours" is only useful next to "check the pot is not standing
    in its own runoff".
    """

    kind: IssueKind
    label: str
    detail: str
    value: float | None = None

    def as_item(self, plant: str, display: str) -> dict[str, object]:
        """Feed shape. Plain types only — a consumer should not need to know
        anything about this component to read it."""
        return {
            "plant": plant,
            "name": display,
            "kind": self.kind.value,
            "label": self.label,
            "detail": self.detail,
            "value": self.value,
        }
