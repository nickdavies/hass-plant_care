"""The outstanding feed, rendered.

Every dashboard that shows the feed shows the same list, and only one of them
is generated from here — the hand-written household dashboards show it too. A
card that renders `items` itself is a dozen lines of Jinja that has to know
which fields each item kind carries, and a copy of it on a second dashboard is
one that will not be updated when an item grows a field.

So the rendering happens once, here, and a card is a single `state_attr` call.

Items are the plain dicts `feed.py` assembles. They come from several sources
with different shapes — a care task carries days and an interval, a fault
carries a remedy, a frozen killswitch carries no plant at all — so every field
but `name` and `label` is tested before it is used.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

NOTHING = "Nothing outstanding."


def outstanding(items: Sequence[Mapping[str, Any]]) -> str:
    """One markdown list item per feed item, with its detail beneath it."""
    if not items:
        return NOTHING

    lines: list[str] = []
    for item in items:
        line = f"- **{item['name']}** — {item['label']}"

        days = item.get("days")
        if days is not None:
            every = item.get("every")
            interval = f", every {every}d" if every is not None else ""
            line += f" ({days:.0f}d ago{interval})"
        lines.append(line)

        detail = item.get("detail")
        if detail is not None:
            # Indented: a lazy continuation of the line above, so the remedy
            # renders as part of the fault it belongs to rather than as a
            # paragraph between two list items.
            lines.append(f"  {detail}")

    return "\n".join(lines)
