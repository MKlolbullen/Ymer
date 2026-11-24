"""Helpers for building users, workspaces and pages directly in the test database."""

import uuid
from dataclasses import dataclass

from flask import Flask

from ymer.auth import hash_password, issue_token_pair
from ymer.db import db_session
from ymer.models import Page, PagePermission, PageType, Role, User, Workspace, WorkspaceMember

PASSWORD = "correct horse battery staple"
_PASSWORD_HASH: str | None = None


def _password_hash() -> str:
    global _PASSWORD_HASH  # hashing is slow; do it once per run
    if _PASSWORD_HASH is None:
        _PASSWORD_HASH = hash_password(PASSWORD)
    return _PASSWORD_HASH


@dataclass(eq=False)  # hashable by identity, so actors can be dict keys
class Actor:
    """A user plus a ready-made Authorization header."""

    user: User
    headers: dict[str, str]

    @property
    def id(self) -> uuid.UUID:
        return self.user.id


def make_user(app: Flask, name: str) -> Actor:
    user = User(
        email=f"{name}-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=_password_hash(),
        display_name=name.title(),
    )
    db_session.add(user)
    db_session.commit()
    with app.app_context():
        tokens = issue_token_pair(user.id, user.token_version)
    db_session.add(user)  # leaving the app context removed the session; re-attach
    return Actor(user=user, headers={"Authorization": f"Bearer {tokens['access_token']}"})


def make_workspace(owner: Actor, members: dict[Actor, Role] | None = None) -> Workspace:
    workspace = Workspace(name="Test workspace", created_by_id=owner.id)
    workspace.members.append(WorkspaceMember(user_id=owner.id, role=Role.OWNER))
    for actor, role in (members or {}).items():
        workspace.members.append(WorkspaceMember(user_id=actor.id, role=role))
    db_session.add(workspace)
    db_session.commit()
    return workspace


def make_page(
    workspace: Workspace,
    title: str = "Page",
    parent: Page | None = None,
    type: PageType = PageType.DOCUMENT,
) -> Page:
    page = Page(
        workspace_id=workspace.id,
        parent_id=parent.id if parent else None,
        type=type,
        title=title,
    )
    db_session.add(page)
    db_session.commit()
    return page


def grant(page: Page, actor: Actor, role: Role) -> PagePermission:
    permission = PagePermission(page_id=page.id, user_id=actor.id, role=role)
    db_session.add(permission)
    db_session.commit()
    return permission
