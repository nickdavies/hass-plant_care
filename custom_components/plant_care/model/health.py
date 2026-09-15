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

    # Light. Same family because they fail the same way — silently, with the
    # plant looking fine right up until it does not.
    LUX_SILENT = "lux_silent"

    LIGHT_DEFICIT_TODAY = "light_deficit_today"
    LIGHT_EXCESS_TODAY = "light_excess_today"
    """Today, and already certain — not a projection. See `dli.today_certainty`."""

    LIGHT_CRITICAL = "light_critical"
    """Fast burn, or a day outside the survival bounds. Go and look now."""

    LIGHT_BUDGET_BURNING = "light_budget_burning"
    """Slow burn. Adjust the schedule; nothing is dying this week."""

    LIGHT_UNSTABLE = "light_unstable"
    LIGHT_HOURS_DEVIATION = "light_hours_deviation"
    AUTOMATION_FROZEN = "automation_frozen"


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

    def as_item(
        self, plant: str | None = None, display: str | None = None
    ) -> dict[str, object]:
        """Feed shape. Plain types only — a consumer should not need to know
        anything about this component to read it.

        Both arguments are optional because not every fault belongs to a plant:
        a killswitch somebody left on is a fault of the system, and inventing a
        plant to hang it on would put it in the wrong place on every dashboard
        that groups by plant. Such an item carries `plant: None`, and its label
        names what is actually wrong.
        """
        return {
            "plant": plant,
            "name": display if display is not None else self.label,
            "kind": self.kind.value,
            "label": self.label,
            "detail": self.detail,
            "value": self.value,
        }
