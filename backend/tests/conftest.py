"""Shared pytest fixtures.

Tests run with ``YMER_ENV=test``, so the app connects to ``YMER_TEST_DATABASE_URL``
(the ``ymer_test`` database created by Docker Compose). Tests marked ``db`` are
skipped when that database is unreachable, so the rest of the suite still runs
without Docker.
"""

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

os.environ["YMER_ENV"] = "test"

from ymer import create_app  # noqa: E402
from ymer.config import get_settings  # noqa: E402
from ymer.db import SessionLocal, db_session, get_engine  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parent.parent
TEST_SECRET_KEY = "test-secret-key-that-is-at-least-32-bytes-long"
TEST_COLLAB_SECRET = "test-collab-secret"


@pytest.fixture(scope="session")
def database_available() -> bool:
    engine = create_engine(get_settings().test_database_url, connect_args={"connect_timeout": 2})
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except OperationalError:
        return False
    finally:
        engine.dispose()


@pytest.fixture(autouse=True)
def _skip_without_database(request, database_available):
    if request.node.get_closest_marker("db") and not database_available:
        pytest.skip("Postgres not reachable at YMER_TEST_DATABASE_URL (run `docker compose up -d`)")


@pytest.fixture
def app():
    app = create_app(
        {
            "env": "test",
            "secret_key": TEST_SECRET_KEY,
            "collab_secret": TEST_COLLAB_SECRET,
            # Tests never reach a real model, whatever .env says.
            "ai_provider": "fake",
            "ai_embedding_provider": "fake",
            "ai_embedding_model": "hash-256",
            "ai_index_inline": False,
            "ai_rate_limit": 0,
            # Builds never touch Docker in tests; tests run queued builds themselves.
            "factory_sandbox": "fake",
            "factory_run_inline": False,
        }
    )
    yield app
    app.extensions["sqlalchemy_engine"].dispose()


@pytest.fixture
def client(app):
    return app.test_client()


def alembic_config() -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.attributes["configure_logger"] = False
    return config


@pytest.fixture(scope="session")
def migrated_database(database_available):
    """Rebuild the test database schema from the migrations once per test run."""
    if not database_available:
        pytest.skip("Postgres not reachable at YMER_TEST_DATABASE_URL (run `docker compose up -d`)")
    config = alembic_config()
    command.downgrade(config, "base")
    command.upgrade(config, "head")


@pytest.fixture
def db(app, migrated_database):
    """A session whose work is rolled back after the test.

    The test and every request it makes share one connection inside an outer
    transaction; ``commit()`` in app code only releases a savepoint, so nothing
    leaks between tests.
    """
    engine = get_engine(app)
    connection = engine.connect()
    outer = connection.begin()
    SessionLocal.configure(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield db_session
    finally:
        db_session.remove()
        outer.rollback()
        connection.close()
        SessionLocal.configure(bind=engine, join_transaction_mode="conservative_savepoint")
