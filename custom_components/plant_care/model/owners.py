"""Who a plant belongs to, and whose phone that is.

An owner is a person or a group; a name is a group iff it is a key of `groups`.
`groups` is the household file shared with `light_motion_profiles`, so it may
name groups and members this component never uses.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

DEFAULT_PERSON_ICON = "mdi:account"
"""The tab icon for a person who did not choose one."""


@dataclass(frozen=True)
class Owner:
    """One row of `owners`: where their items go, and how they are drawn."""

    action: str
    """The notify action their feed items are pushed to."""

    icon: str | None
    """The icon on their dashboard tab. People only — a group has no tab."""


@dataclass(frozen=True)
class Owners:
    entries: Mapping[str, Owner]
    """Owner name to what is known about them, people and groups alike."""

    groups: Mapping[str, tuple[str, ...]]

    system_notify: str
    """Where a fault belonging to no plant goes."""

    def __post_init__(self) -> None:
        # Every group in `entries` must be made of people in `entries`: each
        # member gets a tab and a sensor. Groups only in the shared file are
        # not checked.
        for name, owner in self.entries.items():
            if not self.is_group(name):
                continue
            if owner.icon is not None:
                raise ValueError(
                    f"owner '{name}' is a group with an icon, but only people get "
                    "a dashboard tab for one to appear on"
                )
            members = self.groups[name]
            if not members:
                raise ValueError(
                    f"owner '{name}' is a group with no members, so nobody would "
                    "be told"
                )
            for member in members:
                if self.is_group(member):
                    raise ValueError(
                        f"owner '{name}' is a group whose member '{member}' is "
                        "itself a group; a group may only contain people"
                    )
                if member not in self.entries:
                    raise ValueError(
                        f"owner '{name}' is a group whose member '{member}' has "
                        "no notify action and no dashboard"
                    )

    def __contains__(self, name: str) -> bool:
        return name in self.entries

    def is_group(self, name: str) -> bool:
        return name in self.groups

    def people(self) -> tuple[str, ...]:
        return tuple(name for name in self.entries if not self.is_group(name))

    def members(self, name: str) -> tuple[str, ...]:
        """The group's members, or the person alone."""
        if self.is_group(name):
            return self.groups[name]
        return (name,)

    def action(self, owner: str) -> str:
        return self.entries[owner].action

    def icon(self, person: str) -> str:
        return self.entries[person].icon or DEFAULT_PERSON_ICON

    def route(self, owner: str | None) -> str:
        """The action an item goes to: its owner's, or `system_notify` when it
        belongs to no plant."""
        if owner is None:
            return self.system_notify
        return self.entries[owner].action
