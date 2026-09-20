"""Constants shared across the platform modules.

Here rather than in `__init__.py` so a platform can import them without
importing the package root, which would be circular.
"""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "plant_care"

SIGNAL_CARE_UPDATED = f"{DOMAIN}_care_updated"
"""Fired when a care task is marked done.

The days-since and due entities read the care log but are not subscribed to it,
and a button press is the only thing that changes it — so one dispatcher signal
is simpler than making the log observable for a single event type.
"""

ATTR_ITEMS = "items"
"""The outstanding feed's list attribute.

The integration point for anything downstream — a dashboard card today, a bridge
to an external task system later. Named once so both ends agree.
"""

ATTR_MARKDOWN = "markdown"
"""The same feed, rendered — see `model/markdown.py`.

Carried on the sensor rather than built by each card, because a card that
renders `items` itself has to know what every item kind carries, and there is
more than one dashboard showing this list.
"""

ATTR_PLANT = "plant"
ATTR_TASK = "task"
ATTR_WHEN = "when"

SERVICE_RECORD_WATERING = "record_watering"
"""Record a watering the probe did not see, or correct the date of one.

Not a button, on purpose: a button on the dashboard would be a second source
of truth competing with the detector. This is the escape hatch for the two
cases the detector cannot cover — a watering during a restart, and history
that predates the component.
"""

RECOMPUTE_INTERVAL = timedelta(minutes=30)
"""How often the time-driven quantities are re-read.

Days-since only changes meaningfully once an hour; polling faster would burn
state writes to move a one-decimal number that barely moves. The feed and the
notifier share it so they cannot disagree about when a task became overdue.
"""
