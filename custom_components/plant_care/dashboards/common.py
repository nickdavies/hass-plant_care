"""What every part of the Plants dashboard shares: where things live, whose
plants a card covers, and the few cards and rows too small to own a module."""

from __future__ import annotations

from dataclasses import dataclass
from string import Template

from custom_components.lovelace_codegen import ICON, NAME, MarkdownCard

from ..model import Entity, LightFixture, Plant, PlantCareConfig, naming

DASHBOARD = "plants"
"""The generated dashboard's url path. Links to its subviews are absolute, so
the same card embedded on another dashboard still opens them."""


def plant_path(plant: Plant) -> str:
    """The plant's subview, by path within the dashboard.

    A hyphen, which no slug can contain, so a plant can never share a path with
    a person or a tab.
    """
    return f"plant-{plant.name}"


def plant_link(plant: Plant) -> str:
    return f"/{DASHBOARD}/{plant_path(plant)}"


@dataclass(frozen=True)
class Scope:
    """The plants a card covers, the lamps over them, and the feed that lists
    what they need: everyone's, or one person's."""

    plants: tuple[Plant, ...]
    fixtures: tuple[LightFixture, ...]
    feed: Entity


def scope(config: PlantCareConfig, person: str | None = None) -> Scope:
    if person is None:
        return Scope(config.plants, config.lights, naming.outstanding())
    return Scope(
        config.plants_for(person),
        config.fixtures_for_person(person),
        naming.person_outstanding(person),
    )


# The list arrives already rendered, in `model/markdown.py`. This card is not
# the only one showing it — the household dashboards show the same feed — and a
# copy of the Jinja on each is one that will not be updated when an item grows a
# field. The count in the heading is the sensor's own state.
# A `Template` rather than `str.format`, because the body is Jinja.
FEED = Template("""## Needs attention ({{ states('$entity') }})

{{ state_attr('$entity', 'markdown') }}""")


def feed_card(entity: Entity) -> MarkdownCard:
    return MarkdownCard(FEED.substitute(entity=entity.full))


NOTHING_YET = "### No plants are yours yet"


def nothing(heading: str) -> MarkdownCard:
    """In place of a card with nothing to show, so an empty page does not look
    like one that failed to render."""
    return MarkdownCard(f"### {heading}")


def text_row(name: str, text: str, icon: str) -> dict[str, str]:
    """A row inside an EntitiesCard showing a fixed string rather than an entity.

    Lives here rather than in `lovelace_codegen` because this is the only
    dashboard that needs it. Move it there the day the second one does.
    """
    return {"type": "text", NAME: name, "text": text, ICON: icon}


def fixture_title(fixture: LightFixture) -> str:
    return f"{fixture.name.replace('_', ' ').title()} lamp"
