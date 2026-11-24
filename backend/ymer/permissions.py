"""Workspace-scoped data access and the permission rules.

Every query for workspace content goes through a :class:`WorkspaceScope`, which is
built for one user and one workspace and filters by ``workspace_id`` itself, so a
handler cannot accidentally read another workspace's rows.

Effective role of a user on a page:

1. A workspace **owner** is owner of every page; page grants never narrow an owner.
2. Otherwise the **nearest explicit grant** (``page_permissions``) on the page or its
   closest ancestor wins. It may widen access (share a page with a guest who is not a
   workspace member) or narrow it (make a subtree view-only for an editor).
3. Otherwise the user's **workspace role** applies.
4. Otherwise the user has no access, and the page is reported as not found.

What each role allows on a page: viewer reads, commenter also comments, editor also
edits, creates children, moves, deletes and shares (up to their own role).
"""

import uuid
from dataclasses import dataclass, field

from sqlalchemy import and_, exists, func, literal, select
from sqlalchemy.orm import Session, aliased
from werkzeug.exceptions import Forbidden, NotFound

from ymer.models import Page, PagePermission, Role, User, Workspace, WorkspaceMember
from ymer.models.workspace import role_enum


def workspaces_for_user(session: Session, user: User) -> list[tuple[Workspace, Role | None]]:
    """Workspaces the user belongs to, plus ones where they hold a page grant (role None)."""
    member_rows = session.execute(
        select(Workspace, WorkspaceMember.role)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(WorkspaceMember.user_id == user.id)
        .order_by(Workspace.created_at)
    ).all()
    guest_rows = session.scalars(
        select(Workspace)
        .where(
            Workspace.id.in_(
                select(Page.workspace_id)
                .join(PagePermission, PagePermission.page_id == Page.id)
                .where(PagePermission.user_id == user.id)
            ),
            ~exists().where(
                WorkspaceMember.workspace_id == Workspace.id,
                WorkspaceMember.user_id == user.id,
            ),
        )
        .order_by(Workspace.created_at)
    ).all()
    return [(w, r) for w, r in member_rows] + [(w, None) for w in guest_rows]


@dataclass
class WorkspaceScope:
    session: Session
    user: User
    workspace: Workspace
    # The user's workspace role; None for a guest who only has page grants.
    role: Role | None
    _page_roles: dict[uuid.UUID, Role | None] = field(default_factory=dict, repr=False)

    # --- construction -----------------------------------------------------------------

    @classmethod
    def load(cls, session: Session, user: User, workspace_id: uuid.UUID) -> "WorkspaceScope":
        """Scope for ``user`` in ``workspace_id``; 404 if they have no access at all."""
        row = session.execute(
            select(Workspace, WorkspaceMember.role)
            .outerjoin(
                WorkspaceMember,
                and_(
                    WorkspaceMember.workspace_id == Workspace.id,
                    WorkspaceMember.user_id == user.id,
                ),
            )
            .where(Workspace.id == workspace_id)
        ).first()
        if row is None:
            raise NotFound("Workspace not found.")
        workspace, role = row
        if role is None:
            has_grant = session.scalar(
                select(
                    exists().where(
                        PagePermission.user_id == user.id,
                        PagePermission.page_id == Page.id,
                        Page.workspace_id == workspace_id,
                    )
                )
            )
            if not has_grant:
                # Same answer as a missing workspace, so ids cannot be probed.
                raise NotFound("Workspace not found.")
        return cls(session=session, user=user, workspace=workspace, role=role)

    # --- workspace-level checks -------------------------------------------------------

    @property
    def is_member(self) -> bool:
        return self.role is not None

    @property
    def is_owner(self) -> bool:
        return self.role is Role.OWNER

    def require_workspace_role(self, minimum: Role) -> None:
        if self.role is None or not self.role.at_least(minimum):
            raise Forbidden(f"Requires the {minimum.value} role in this workspace.")

    # --- page queries -----------------------------------------------------------------

    def pages_query(self):
        """Base select for pages, always restricted to this workspace."""
        return select(Page).where(Page.workspace_id == self.workspace.id)

    def _nearest_grant(self, page_id: uuid.UUID) -> Role | None:
        chain = (
            select(Page.id, Page.parent_id, literal(0).label("depth"))
            .where(Page.id == page_id, Page.workspace_id == self.workspace.id)
            .cte("chain", recursive=True)
        )
        parent = aliased(Page)
        chain = chain.union_all(
            select(parent.id, parent.parent_id, chain.c.depth + 1).join(
                chain, parent.id == chain.c.parent_id
            )
        )
        return self.session.scalar(
            select(PagePermission.role)
            .join(chain, PagePermission.page_id == chain.c.id)
            .where(PagePermission.user_id == self.user.id)
            .order_by(chain.c.depth)
            .limit(1)
        )

    def _effective(self, grant: Role | None) -> Role | None:
        if self.is_owner:
            return Role.OWNER
        return grant if grant is not None else self.role

    def page_role(self, page: Page) -> Role | None:
        """The user's effective role on ``page`` (which must belong to this workspace)."""
        if page.workspace_id != self.workspace.id:
            return None
        if page.id not in self._page_roles:
            grant = None if self.is_owner else self._nearest_grant(page.id)
            self._page_roles[page.id] = self._effective(grant)
        return self._page_roles[page.id]

    def forget_page_roles(self) -> None:
        """Drop cached roles, e.g. after a page moved and its inherited grants changed."""
        self._page_roles.clear()

    def get_page(self, page_id: uuid.UUID, minimum: Role = Role.VIEWER) -> Page:
        """Fetch a page in this workspace the user may access with at least ``minimum``.

        404 if it does not exist here or the user cannot see it; 403 if they can see it
        but their role is too low.
        """
        page = self.session.scalar(self.pages_query().where(Page.id == page_id))
        role = self.page_role(page) if page is not None else None
        if page is None or role is None:
            raise NotFound("Page not found.")
        if not role.at_least(minimum):
            raise Forbidden(f"Requires the {minimum.value} role on this page.")
        return page

    def visible_pages(
        self, parent_id: uuid.UUID | None = None, *, roots_only: bool = False
    ) -> list[tuple[Page, Role]]:
        """Pages the user can see, with their effective role, in tree order.

        One recursive query walks the tree top-down carrying the nearest grant.
        ``parent_id`` limits the result to that page's children; ``roots_only`` to
        top-level pages.
        """
        if self.is_owner:
            stmt = self.pages_query()
        else:
            grant = aliased(PagePermission)
            tree = (
                select(Page.id, grant.role.label("grant"))
                .outerjoin(grant, and_(grant.page_id == Page.id, grant.user_id == self.user.id))
                .where(Page.workspace_id == self.workspace.id, Page.parent_id.is_(None))
                .cte("tree", recursive=True)
            )
            child = aliased(Page)
            child_grant = aliased(PagePermission)
            tree = tree.union_all(
                select(child.id, func.coalesce(child_grant.role, tree.c.grant))
                .join(tree, child.parent_id == tree.c.id)
                .outerjoin(
                    child_grant,
                    and_(child_grant.page_id == child.id, child_grant.user_id == self.user.id),
                )
            )
            member_role = literal(self.role.value if self.role else None, type_=role_enum)
            effective = func.coalesce(tree.c.grant, member_role)
            stmt = (
                select(Page, effective)
                .join(tree, tree.c.id == Page.id)
                .where(effective.is_not(None))
            )
        if parent_id is not None:
            stmt = stmt.where(Page.parent_id == parent_id)
        elif roots_only:
            stmt = stmt.where(Page.parent_id.is_(None))
        stmt = stmt.order_by(Page.position, Page.created_at)

        result: list[tuple[Page, Role]] = []
        if self.is_owner:
            result = [(page, Role.OWNER) for page in self.session.scalars(stmt)]
        else:
            result = [(page, Role(role)) for page, role in self.session.execute(stmt)]
        for page, role in result:
            self._page_roles[page.id] = role
        return result

    def is_descendant(self, candidate_id: uuid.UUID, ancestor_id: uuid.UUID) -> bool:
        """True if ``candidate_id`` is ``ancestor_id`` or lies beneath it."""
        sub = (
            select(Page.id)
            .where(Page.id == ancestor_id, Page.workspace_id == self.workspace.id)
            .cte("sub", recursive=True)
        )
        child = aliased(Page)
        sub = sub.union_all(select(child.id).join(sub, child.parent_id == sub.c.id))
        return bool(self.session.scalar(select(exists().where(sub.c.id == candidate_id))))
