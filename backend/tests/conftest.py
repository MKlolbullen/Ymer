"""Shared pytest fixtures.

Tests run with ``YMER_ENV=test``, so the app connects to ``YMER_TEST_DATABASE_URL``
(the ``ymer_test`` database created by Docker Compose). Tests marked ``db`` are
skipped when that database is unreachable, so the rest of the suite still runs
without Docker.
"""

import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

os.environ["YMER_ENV"] = "test"

from ymer import create_app  # noqa: E402
from ymer.config import get_settings  # noqa: E402


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
    app = create_app({"env": "test"})
    yield app
    app.extensions["sqlalchemy_engine"].dispose()


@pytest.fixture
def client(app):
    return app.test_client()
