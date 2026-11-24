"""The migration matches the models and the database enforces the core invariants."""

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from tests.conftest import alembic_config
from tests.factories import make_page, make_user, make_workspace
from ymer.db import Base, get_engine
from ymer.models import Page, PagePermission, PageType, Role, User

pytestmark = pytest.mark.db


def test_migrations_match_models(app, migrated_database):
    with get_engine(app).connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []


def test_downgrade_and_upgrade_again(app, migrated_database):
    config = alembic_config()
    command.downgrade(config, "0001")
    with get_engine(app).connect() as conn:
        assert conn.scalar(text("SELECT to_regclass('public.users')")) is None
        assert conn.scalar(text("SELECT count(*) FROM pg_type WHERE typname = 'member_role'")) == 0
    command.upgrade(config, "head")


def test_email_must_be_lowercase(db):
    db.add(User(email="Mixed@Example.com", password_hash="x", display_name="M"))
    with pytest.raises(IntegrityError):
        db.flush()


def test_email_is_unique(db):
    db.add(User(email="same@example.com", password_hash="x", display_name="A"))
    db.flush()
    db.add(User(email="same@example.com", password_hash="x", display_name="B"))
    with pytest.raises(IntegrityError):
        db.flush()


def test_parent_must_be_in_same_workspace(app, db):
    owner = make_user(app, "owner")
    ws_a, ws_b = make_workspace(owner), make_workspace(owner)
    parent = make_page(ws_a)
    db.add(Page(workspace_id=ws_b.id, parent_id=parent.id, type=PageType.DOCUMENT))
    with pytest.raises(IntegrityError, match="fk_pages_parent_same_workspace"):
        db.flush()


def test_page_cannot_be_its_own_parent(app, db):
    owner = make_user(app, "owner")
    page = make_page(make_workspace(owner))
    page.parent_id = page.id
    with pytest.raises(IntegrityError, match="ck_pages_not_own_parent"):
        db.flush()


def test_page_grant_cannot_be_owner(app, db):
    owner = make_user(app, "owner")
    page = make_page(make_workspace(owner))
    db.add(PagePermission(page_id=page.id, user_id=owner.id, role=Role.OWNER))
    with pytest.raises(IntegrityError, match="ck_page_permissions_role_not_owner"):
        db.flush()


def test_deleting_a_page_deletes_its_subtree_and_grants(app, db):
    owner, guest = make_user(app, "owner"), make_user(app, "guest")
    ws = make_workspace(owner)
    root = make_page(ws, "root")
    child = make_page(ws, "child", parent=root)
    db.add(PagePermission(page_id=child.id, user_id=guest.id, role=Role.VIEWER))
    db.commit()
    db.delete(root)
    db.commit()
    db.expunge_all()  # read from the database, not the identity map
    assert db.get(Page, child.id) is None
    assert db.query(PagePermission).filter_by(user_id=guest.id).count() == 0
