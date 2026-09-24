"""Measuring one plant's daily light integral, and judging it.

One coordinator per plant that has a lux fixture. Every minute it reads the
fixture's members, averages the live ones, converts to PPFD with whichever
factor applies right now, and folds the result into the day's running total. At
local midnight the day is closed out and appended to the history the burn-rate
engine reads.

The judgement is all in `model.dli` — bands, budgets, burn rates, what counts as
certain today. This is the wiring that gets a number to it once a minute.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta

from homeassistant.const import STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .model import (
    DailyDli,
    Direction,
    DliAccumulator,
    LightFixture,
    LuxFixture,
    Plant,
    ppfd_factor,
)
from .model.dli import BurnKind, evaluate, outside_survival, today_certainty, unstable
from .model.health import HealthIssue, IssueKind
from .store import EventLog

_LOGGER = logging.getLogger(__name__)

NON_VALUES = {STATE_UNAVAILABLE, STATE_UNKNOWN, "none", ""}

SAMPLE_INTERVAL = timedelta(minutes=1)
"""A minute's resolution on a quantity integrated over a day is a rounding
error, and it keeps every held interval well inside `MAX_SAMPLE_GAP`."""

FLUSH_INTERVAL = timedelta(minutes=5)
"""How often today's partial total is written down.

Not on every sample — that would be a storage write a minute per plant. Not only
at midnight either: a restart at four in the afternoon would then lose the whole
day, and a day that reads as zero because nobody was recording is exactly the
kind of fabricated failure the budget must never be spent on.
"""

LUX_SILENT_AFTER = timedelta(hours=6)
"""Every member of a lux fixture quiet this long is a fault in its own right.

Without this the symptom still shows up — a day with no readings accumulates
nothing and burns budget hard — but it shows up as "this plant is in the dark",
which sends you to the lamp instead of to the sensor.
"""


class DliCoordinator:
    """Accumulates and judges one plant's light."""

    def __init__(
        self,
        hass: HomeAssistant,
        plant: Plant,
        lux: LuxFixture,
        fixtures: Sequence[LightFixture],
        event_log: EventLog,
    ) -> None:
        if plant.dli is None:
            raise ValueError(f"plant '{plant.name}' has no dli objective")

        self._hass = hass
        self._plant = plant
        self._lux = lux
        self._fixtures = tuple(fixtures)
        self._event_log = event_log
        self._objective = plant.dli
        self._listeners: list[Callable[[], None]] = []
        self._unsubs: list[Callable[[], None]] = []

        self._accumulator = DliAccumulator(dt_util.now().date())
        self._last_lux: float | None = None
        self._lux_seen_at: datetime | None = None
        self._flushed_at: datetime | None = None
        self._started_at = dt_util.utcnow()

    @property
    def plant(self) -> Plant:
        return self._plant

    @property
    def lux_fixture(self) -> LuxFixture:
        return self._lux

    @property
    def today(self) -> float:
        return round(self._accumulator.total, 3)

    @property
    def lux(self) -> float | None:
        """The fixture's members averaged, or `None` while they are all out."""
        return self._last_lux

    def history(self) -> list[DailyDli]:
        return [
            DailyDli(day=day, value=value)
            for day, value in sorted(
                self._event_log.dli_history(self._plant.name).items()
            )
        ]

    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(listener)

        def remove() -> None:
            self._listeners.remove(listener)

        return remove

    @callback
    def _notify(self) -> None:
        for listener in self._listeners:
            listener()

    async def async_start(self) -> None:
        """Resume today where the last run left off, then start sampling."""
        today = dt_util.now().date()
        stored = self._event_log.dli_history(self._plant.name).get(today)
        self._accumulator = DliAccumulator(today, stored or 0.0)

        self._sample()
        self._unsubs.append(
            async_track_time_interval(
                self._hass, self._handle_tick, SAMPLE_INTERVAL, cancel_on_shutdown=True
            )
        )
        self._notify()

    @callback
    def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()

    @callback
    def _handle_tick(self, _now: datetime) -> None:
        self._sample()
        self._notify()

    # ---- Reading -------------------------------------------------------

    def _read(self, entity_id: str) -> float | None:
        state = self._hass.states.get(entity_id)
        if state is None or state.state in NON_VALUES:
            return None
        try:
            return float(state.state)
        except (TypeError, ValueError):
            return None

    def _lit_by(self) -> list[float]:
        """The factors of the lamps currently on over this plant.

        Only consulted for a mixed plant. A sun-only plant has no lamp to switch
        to, and a grow-only plant has no lux reading to convert in the first
        place.
        """
        if not self._plant.is_mixed_light:
            return []

        factors: list[float] = []
        for fixture in self._fixtures:
            state = self._hass.states.get(fixture.switch_entity)
            if state is None or state.state != STATE_ON:
                continue
            if fixture.lux_to_ppfd is None:
                # Rejected at parse for a mixed plant, so reaching here means
                # the document and this component have diverged.
                continue
            factors.append(fixture.lux_to_ppfd)
        return factors

    @callback
    def _sample(self, now: datetime | None = None) -> None:
        now = now or dt_util.now()

        average = self._lux.average(
            [self._read(entity) for entity in self._lux.entities]
        )
        self._last_lux = average

        if average is None:
            ppfd = None
        else:
            self._lux_seen_at = now
            ppfd = average * ppfd_factor(self._lux.sun_lux_to_ppfd, self._lit_by())

        finished = self._accumulator.observe(now, ppfd)
        if finished is not None:
            self._hass.async_create_task(self._async_persist(finished))
            self._flushed_at = now
            return

        if self._flushed_at is None or now - self._flushed_at >= FLUSH_INTERVAL:
            self._flushed_at = now
            self._hass.async_create_task(
                self._async_persist(
                    DailyDli(day=self._accumulator.day, value=self._accumulator.total)
                )
            )

    async def _async_persist(self, day: DailyDli) -> None:
        # One extra day beyond the window so the oldest day in the window still
        # has a predecessor while the rollover is in flight.
        await self._event_log.async_record_dli(
            self._plant.name,
            day.day,
            day.value,
            keep_days=self._objective.window_days + 1,
        )

    # ---- Judging -------------------------------------------------------

    def alerts(self) -> list[HealthIssue]:
        """Everything wrong with this plant's light, worst first."""
        now = dt_util.now()
        history = self.history()
        band = self._objective.preferred
        issues: list[HealthIssue] = []

        silent = self._lux_silent(now)
        if silent is not None:
            # A fixture nobody can read makes every number below meaningless, so
            # it is the only thing worth saying until it is fixed.
            return [silent]

        critical = self._critical(history)
        issues.extend(critical)
        issues.extend(self._today(now, history))

        for alert in evaluate(history, self._objective, now.date()):
            if alert.kind is BurnKind.FAST:
                continue  # already covered by `_critical`
            if critical:
                # A day bad enough to page is also inside the seven-day window,
                # so both burn rates fire on the same evidence. Reporting both
                # would put two items in the feed for one dark day, and "adjust
                # the schedule" is not advice worth reading next to "go and look
                # now". The slow signal is for weeks nothing else notices.
                continue
            issues.append(
                HealthIssue(
                    kind=IssueKind.LIGHT_BUDGET_BURNING,
                    label=f"Light budget burning {alert.direction.value}",
                    detail=(
                        f"Over the last {alert.days} days this plant has run "
                        f"{alert.deviation:.1f} mol/m² outside its {band.low:g}–"
                        f"{band.high:g} band, which is {alert.rate:.1f}× the pace its "
                        f"{self._objective.budget:g} mol/m² budget allows. Adjust the "
                        "schedule or the position; nothing is dying this week."
                    ),
                    value=alert.rate,
                )
            )

        spread = unstable(history, self._objective, now.date())
        if spread is not None:
            issues.append(
                HealthIssue(
                    kind=IssueKind.LIGHT_UNSTABLE,
                    label="Light swinging wildly",
                    detail=(
                        f"Daily light has ranged over {spread:.1f} mol/m² in the last "
                        f"{self._objective.window_days} days. The average may look "
                        "fine; find what is changing — a blind, a moved pot, a lamp "
                        "that is not switching reliably."
                    ),
                    value=spread,
                )
            )

        return issues

    def _lux_silent(self, now: datetime) -> HealthIssue | None:
        # A grace period from start-up: MQTT discovery has not necessarily
        # created the lux entities yet, and a component that shouts about its
        # own cold start teaches you to ignore it.
        if dt_util.utcnow() - self._started_at < LUX_SILENT_AFTER:
            return None
        if self._lux_seen_at is not None and now - self._lux_seen_at < LUX_SILENT_AFTER:
            return None

        return HealthIssue(
            kind=IssueKind.LUX_SILENT,
            label="Light sensor silent",
            detail=(
                f"No member of lux fixture '{self._lux.name}' has reported in "
                f"{LUX_SILENT_AFTER.total_seconds() / 3600:.0f} hours. Nothing is "
                "measuring this plant's light, so its DLI figures are not to be "
                "believed until this is fixed."
            ),
        )

    def _critical(self, history: list[DailyDli]) -> list[HealthIssue]:
        """Fast burn, or a completed day outside survival.

        Either alone is enough. The survival check is not redundant: a plant
        whose band sits well inside generous survival bounds can breach survival
        on a day the burn rate barely notices, and a plant with a tight budget
        can burn fast on a day that was never dangerous.
        """
        now = dt_util.now()
        last = next((day for day in reversed(history) if day.day < now.date()), None)
        if last is None:
            return []

        fast = next(
            (
                alert
                for alert in evaluate(history, self._objective, now.date())
                if alert.kind is BurnKind.FAST
            ),
            None,
        )
        breached = outside_survival(last.value, self._objective)
        if fast is None and not breached:
            return []

        band = self._objective.preferred
        direction = (
            fast.direction
            if fast is not None
            else (Direction.UNDER if last.value < band.low else Direction.OVER)
        )
        survival = self._objective.survival
        detail = (
            f"{last.day} delivered {last.value:.1f} mol/m² against a "
            f"{band.low:g}–{band.high:g} band."
        )
        if breached and survival is not None:
            detail += (
                f" That is outside the survival range {survival.low:g}–"
                f"{survival.high:g} this species tolerates at all."
            )
        detail += (
            " Move it, or change the lamp's hours."
            if direction is Direction.UNDER
            else " Shade it, raise the lamp, or shorten its hours."
        )

        return [
            HealthIssue(
                kind=IssueKind.LIGHT_CRITICAL,
                label=f"Light critically {direction.value}",
                detail=detail,
                value=last.value,
            )
        ]

    def _today(self, now: datetime, history: list[DailyDli]) -> list[HealthIssue]:
        certainty = today_certainty(
            self._accumulator.total, now, history, self._objective
        )
        if certainty is None:
            return []

        band = self._objective.preferred
        if certainty is Direction.OVER:
            return [
                HealthIssue(
                    kind=IssueKind.LIGHT_EXCESS_TODAY,
                    label="Too much light today",
                    detail=(
                        f"Today has already delivered {self.today:.1f} mol/m², past "
                        f"the {band.high:g} upper bound, and the day is not over. A "
                        "lamp is probably stuck on."
                    ),
                    value=self.today,
                )
            ]

        return [
            HealthIssue(
                kind=IssueKind.LIGHT_DEFICIT_TODAY,
                label="Today's light is unrecoverable",
                detail=(
                    f"Today has {self.today:.1f} mol/m² and cannot reach the "
                    f"{band.low:g} lower bound even at the best rate this plant has "
                    "managed. Check the lamp is actually lit."
                ),
                value=self.today,
            )
        ]
