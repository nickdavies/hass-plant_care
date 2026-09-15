"""Constants shared across the platform modules.

Here rather than in `__init__.py` so a platform can import them without
importing the package root, which would be circular.
"""

from __future__ import annotations

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

ATTR_PLANT = "plant"
ATTR_TASK = "task"
