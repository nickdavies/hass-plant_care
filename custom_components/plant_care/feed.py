"""Assembling the outstanding feed.

One place that knows what "outstanding" means, so the per-plant count and the
global list cannot disagree about it — they call the same function rather than
one reading the other's state.

Items are plain dicts. That is the contract with everything downstream: a
dashboard card today, a bridge to an external task system later. A consumer
should not have to know anything about this component's types to read it, and
`kind` is enough to route on.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.util import dt as dt_util

from .model import Direction, Plant
from .model.health import IssueKind

if TYPE_CHECKING:
    from . import PlantCareData


def plant_items(data: PlantCareData, plant: Plant) -> list[dict[str, Any]]:
    """Everything this plant currently needs.

    Faults first. A silent probe or an unlit lamp means nothing else about this
    plant can be believed, so it should not be buried under three overdue
    feedings.
    """
    now = dt_util.utcnow()
    items: list[dict[str, Any]] = []

    coordinator = data.coordinators.get(plant.name)
    if coordinator is not None:
        items.extend(
            issue.as_item(plant.name, plant.display, plant.owner)
            for issue in coordinator.health()
        )

    dli = data.dli_coordinators.get(plant.name)
    if dli is not None:
        items.extend(
            issue.as_item(plant.name, plant.display, plant.owner)
            for issue in dli.alerts()
        )
    else:
        # No lux fixture, so nothing measures what this plant actually received
        # and the only evidence available is whether its lamps ran. Which is why
        # a plant *with* lux skips this: it has the better signal, and reporting
        # both would produce two items for one problem.
        items.extend(_on_time_items(data, plant))

    if coordinator is not None and coordinator.needs_water:
        items.append(
            {
                "plant": plant.name,
                "name": plant.display,
                "owner": plant.owner,
                "kind": "needs_water",
                "label": "Needs water",
                "days": data.event_log.days_since_watered(plant.name, now),
            }
        )

    for task in plant.care:
        days = data.event_log.care_days_since(plant.name, task.task, now)
        if not task.is_overdue(days):
            continue
        items.append(
            {
                "plant": plant.name,
                "name": plant.display,
                "owner": plant.owner,
                "kind": "care",
                "task": task.task,
                "label": task.display,
                "days": days,
                "every": task.every_days,
            }
        )

    return items


def _on_time_items(data: PlantCareData, plant: Plant) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for name in plant.lights:
        controller = data.light_controllers.get(name)
        if controller is None:
            continue
        deviation = controller.deviation()
        if deviation is None:
            continue

        # "Over the same stretch" rather than "today": both numbers are measured
        # from whenever the count last opened, which is midnight most days and
        # start-up after an outage long enough that nothing can vouch for what
        # the lamp did during it.
        if deviation.direction is Direction.UNDER:
            label = f"{name} is not running"
            detail = (
                f"'{name}' has been on for {deviation.actual} minutes against the "
                f"{deviation.guaranteed} its window guarantees over the same stretch. "
                "A dead bulb, an outlet that dropped off the network, or automation "
                "somebody froze."
            )
        else:
            label = f"{name} is running long"
            detail = (
                f"'{name}' has been on for {deviation.actual} minutes, past the "
                f"{deviation.possible} its window could ever allow over the same "
                "stretch. The plants under it are getting a photoperiod nobody "
                "chose, plus the heat."
            )

        items.append(
            {
                "plant": plant.name,
                "name": plant.display,
                "owner": plant.owner,
                "kind": IssueKind.LIGHT_HOURS_DEVIATION.value,
                "label": label,
                "detail": detail,
                "value": deviation.minutes,
                "fixture": name,
            }
        )
    return items


def system_items(data: PlantCareData) -> list[dict[str, Any]]:
    """Faults belonging to no single plant.

    Only one today: a killswitch left on long enough to have been forgotten.
    It carries `plant: None`, because attaching it to one of the plants under
    the fixture would hide it from everyone looking at the others, and
    `owner: None` routes it to `system_notify`. The fixture is named so two
    frozen lamps do not share one announce key.
    """
    now = dt_util.utcnow()
    return [
        {**issue.as_item(), "fixture": controller.fixture.name}
        for controller in data.light_controllers.values()
        if (issue := controller.frozen_issue(now)) is not None
    ]


def person_items(data: PlantCareData, person: str) -> list[dict[str, Any]]:
    """One person's plants, owned outright or through a group. System faults
    belong to nobody, so they stay on the shared feed."""
    items: list[dict[str, Any]] = []
    for plant in data.config.plants_for(person):
        items.extend(plant_items(data, plant))
    return items


def all_items(data: PlantCareData) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for plant in data.plants:
        items.extend(plant_items(data, plant))
    items.extend(system_items(data))
    return items
