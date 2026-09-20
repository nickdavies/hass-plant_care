"""Rendering the outstanding feed.

Rendered in Python rather than in each card's Jinja, so these tests are what
stands behind every dashboard showing the list — the generated tabs and the
hand-written household ones alike.
"""

from __future__ import annotations

from custom_components.plant_care.model import markdown

CARE = {
    "plant": "passionfruit",
    "name": "Passionfruit",
    "owner": "nick",
    "kind": "care",
    "task": "feed",
    "label": "Feed",
    "days": 17.4,
    "every": 14,
}

FAULT = {
    "plant": "monstera",
    "name": "Monstera",
    "owner": "nick",
    "kind": "probe_silent",
    "label": "Probe silent",
    "detail": "No report for 6 hours. Check the battery.",
    "value": 6.0,
}

WATER = {
    "plant": "monstera",
    "name": "Monstera",
    "owner": "nick",
    "kind": "needs_water",
    "label": "Needs water",
    "days": None,
}


class TestEmpty:
    def test_nothing_outstanding_says_so(self) -> None:
        """Rather than an empty card, which reads as a broken one."""
        assert markdown.outstanding([]) == "Nothing outstanding."


class TestItemShapes:
    """Items come from several sources with different shapes, so every optional
    field is tested before it is used. A missing one must not break the card."""

    def test_a_care_item_carries_its_age_and_interval(self) -> None:
        assert markdown.outstanding([CARE]) == (
            "- **Passionfruit** — Feed (17d ago, every 14d)"
        )

    def test_days_without_an_interval_omits_it(self) -> None:
        item = {**CARE, "label": "Needs water"}
        del item["every"]
        assert markdown.outstanding([item]) == (
            "- **Passionfruit** — Needs water (17d ago)"
        )

    def test_days_of_none_is_not_rendered_as_a_number(self) -> None:
        """Never watered is a real value, not zero days ago."""
        assert markdown.outstanding([WATER]) == "- **Monstera** — Needs water"

    def test_a_fault_puts_its_remedy_under_it(self) -> None:
        """Indented, so the remedy stays attached to the fault rather than
        rendering as a paragraph between two list items."""
        assert markdown.outstanding([FAULT]) == (
            "- **Monstera** — Probe silent\n  No report for 6 hours. Check the battery."
        )

    def test_an_item_with_only_a_name_and_label_renders(self) -> None:
        """A system fault belongs to no plant and carries neither."""
        item = {"plant": None, "name": "Study shelf", "label": "Automation frozen"}
        assert markdown.outstanding([item]) == "- **Study shelf** — Automation frozen"


class TestOrder:
    def test_items_keep_the_order_the_feed_gave_them(self) -> None:
        """Faults first is decided in `feed.py`; rendering must not reshuffle
        it."""
        rendered = markdown.outstanding([FAULT, CARE])
        assert rendered.splitlines() == [
            "- **Monstera** — Probe silent",
            "  No report for 6 hours. Check the battery.",
            "- **Passionfruit** — Feed (17d ago, every 14d)",
        ]
