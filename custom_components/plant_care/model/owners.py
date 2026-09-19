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

    def is_group(self, name: str) -> bool:
        return name in self.groups

    def is_person(self, name: str) -> bool:
        return name in self.actions and name not in self.groups

    def people(self) -> tuple[str, ...]:
        return tuple(name for name in self.actions if not self.is_group(name))

    def members(self, name: str) -> tuple[str, ...]:
        """The group's members, or the person alone."""
        if self.is_group(name):
            return self.groups[name]
        return (name,)

    def action(self, owner: str) -> str:
        return self.actions[owner]
