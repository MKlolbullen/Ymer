"""SQLAlchemy 2.0 setup: declarative ``Base``, engine and request-scoped session.

Usage in request handlers::

    from ymer.db import db_session
    db_session.execute(select(User))

The session is removed at the end of every app context, so each request gets a
fresh one. Models subclass :class:`Base` and live in ``ymer.models``.
"""

from flask import Flask
from sqlalchemy import Engine, MetaData, create_engine
from sqlalchemy.orm import DeclarativeBase, scoped_session, sessionmaker

# Deterministic constraint names keep Alembic autogenerate diffs stable.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


SessionLocal = sessionmaker(autoflush=False, expire_on_commit=False)
db_session = scoped_session(SessionLocal)


def make_engine(url: str, *, echo: bool = False) -> Engine:
    return create_engine(url, echo=echo, pool_pre_ping=True, future=True)


def init_db(app: Flask, url: str, *, echo: bool = False) -> Engine:
    """Create the engine, bind the session factory and register teardown."""
    engine = make_engine(url, echo=echo)
    SessionLocal.configure(bind=engine)
    app.extensions["sqlalchemy_engine"] = engine

    @app.teardown_appcontext
    def _remove_session(exc: BaseException | None) -> None:
        if exc is not None:
            db_session.rollback()
        db_session.remove()

    return engine


def get_engine(app: Flask) -> Engine:
    return app.extensions["sqlalchemy_engine"]
