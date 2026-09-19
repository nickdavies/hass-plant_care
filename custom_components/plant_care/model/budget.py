"""Time spent in a bad state, over a rolling window, against an allowance.

The shape behind every burn-rate check on a continuously measured signal: a
probe that is not reporting, a pot sitting above field capacity. Each accrues
*time* in the bad state; the objective is that the total over a window stays
inside an allowance; and the burn rate is how fast the allowance is being spent
relative to the pace that would exactly exhaust it by the end of the window.

Why a budget rather than a threshold on the current stretch: a threshold sees
only the episode in front of it, so a probe that drops out for an hour every
night — a dying battery, a marginal link — never trips it, because every
episode heals before it is long enough to count. The budget sees the week.

Pure: driven by injected timestamps, so every duration is testable without
waiting.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class Interval:
    """A closed stretch of time in the bad state."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError(f"interval ends {self.end} before it starts {self.start}")

    def overlap(self, since: datetime, until: datetime) -> timedelta:
        """How much of this interval falls inside `[since, until]`."""
        low = max(self.start, since)
        high = min(self.end, until)
        return max(high - low, timedelta(0))


class TimeBudget:
    """Bad time accrued over a rolling `window`, against an `allowance`.

    Two ways to feed it, because the two signals learn about bad time
    differently. A wet pot is known to be wet at each reading, so `mark` is
    called with the state as it goes and the open stretch is tracked here. A
    silent probe is known to have been silent only once it speaks again, so the
    gap is recorded after the fact with `add`, and the stretch still open at
    evaluation time is passed to `accrued` by the caller, which is the only
    thing that knows when the last report was.
    """

    def __init__(self, window: timedelta, allowance: timedelta) -> None:
        if window <= timedelta(0):
            raise ValueError(f"window must be positive, got {window}")
        if allowance < timedelta(0):
            raise ValueError(f"allowance cannot be negative, got {allowance}")
        self.window = window
        self.allowance = allowance
        self._closed: list[Interval] = []
        self._open_since: datetime | None = None

    # ---- Feeding -------------------------------------------------------

    def mark(self, now: datetime, bad: bool) -> bool:
        """Note the state at `now`. Returns whether a stretch just closed —
        the moment there is something new worth persisting."""
        if bad:
            if self._open_since is None:
                self._open_since = now
            return False
        if self._open_since is None:
            return False
        self._closed.append(Interval(self._open_since, now))
        self._open_since = None
        self._prune(now)
        return True

    def add(self, interval: Interval) -> None:
        """Record a stretch learned about after it ended."""
        self._closed.append(interval)
        self._prune(interval.end)

    def restore(self, closed: Iterable[Interval]) -> None:
        """Re-adopt stretches persisted before a restart.

        A stretch that was still open when the process stopped is not
        restorable — nothing recorded its end — so it is lost. That under-counts,
        which is the safe direction: a budget that forgets an episode stays
        quiet for longer, it never invents one.
        """
        self._closed = sorted(closed, key=lambda interval: interval.start)

    def closed(self) -> list[Interval]:
        return list(self._closed)

    # ---- Reading out ---------------------------------------------------

    def stretches(
        self, now: datetime, over: timedelta, open_since: datetime | None = None
    ) -> list[Interval]:
        """Every bad stretch inside the last `over`, clipped to it, in order.

        `open_since` is a stretch still running at `now` that this object was
        not told about through `mark` — see the class docstring.
        """
        since = now - over
        out: list[Interval] = []
        for interval in self._closed:
            if interval.overlap(since, now) > timedelta(0):
                out.append(Interval(max(interval.start, since), min(interval.end, now)))
        for start in (self._open_since, open_since):
            if start is not None and start < now:
                out.append(Interval(max(start, since), now))
        return sorted(out, key=lambda interval: interval.start)

    def accrued(
        self, now: datetime, over: timedelta, open_since: datetime | None = None
    ) -> timedelta:
        """Bad time inside the last `over`, ending at `now`."""
        return sum(
            (i.end - i.start for i in self.stretches(now, over, open_since)),
            timedelta(0),
        )

    def burn_rate(
        self, now: datetime, over: timedelta, open_since: datetime | None = None
    ) -> float:
        """Accrued bad time as a multiple of the allowance's own pace.

        The allowance is spread evenly across the window, so `allowance × over
        / window` is what "on pace" would have spent in the last `over`. 1.0
        means exactly on track to exhaust the whole allowance over the whole
        window; over the full window itself, 1.0 means the allowance is gone.
        """
        on_pace = self.allowance * (over / self.window)
        if on_pace <= timedelta(0):
            return 0.0
        return self.accrued(now, over, open_since) / on_pace

    def _prune(self, now: datetime) -> None:
        cutoff = now - self.window
        self._closed = [interval for interval in self._closed if interval.end > cutoff]
