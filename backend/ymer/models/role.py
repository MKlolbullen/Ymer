"""Roles shared by workspace membership and page permissions."""

import enum


class Role(enum.StrEnum):
    """Ordered from least to most access. Compare with ``rank``."""

    VIEWER = "viewer"
    COMMENTER = "commenter"
    EDITOR = "editor"
    OWNER = "owner"

    @property
    def rank(self) -> int:
        return _RANK[self]

    def at_least(self, other: "Role") -> bool:
        return self.rank >= other.rank


_RANK = {Role.VIEWER: 0, Role.COMMENTER: 1, Role.EDITOR: 2, Role.OWNER: 3}
