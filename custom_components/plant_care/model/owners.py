"""Who a plant belongs to, and whose phone that is.

An owner is a person or a group; a name is a group iff it is a key of `groups`.
`groups` is the household file shared with `light_motion_profiles`, so it may
name groups and members this component never uses.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class Owners:
    actions: Mapping[str, str]
    """Owner name to notify action, people and groups alike."""

    groups: Mapping[str, tuple[str, ...]]

    system_notify: str
    """Where a fault belonging to no plant goes."""

    def __post_init__(self) -> None:
        # Every group in `actions` must be made of people in `actions`: each
        # member gets a tab and a sensor. Groups only in the shared file are
        # not checked.
        for name in self.actions:
            if not self.is_group(name):
                continue
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
                if member not in self.actions:
                    raise ValueError(
                        f"owner '{name}' is a group whose member '{member}' has "
                        "no notify action and no dashboard"
                    )

    def is_group(self, name: str) -> bool:
        return name in self.groups

    def people(self) -> tuple[str, ...]:
        return tuple(name for name in self.actions if not self.is_group(name))

    def members(self, name: str) -> tuple[str, ...]:
        """The group's members, or the person alone."""
        if self.is_group(name):
            return self.groups[name]
        return (name,)

    def action(self, owner: str) -> str:
        return self.actions[owner]

    def route(self, owner: str | None) -> str:
        """The action an item goes to: its owner's, or `system_notify` when it
        belongs to no plant."""
        if owner is None:
            return self.system_notify
        return self.actions[owner]
