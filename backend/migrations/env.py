"""Alembic environment.

The URL is taken from ``ymer.config`` (``YMER_DATABASE_URL``), or from
``-x db_url=...`` on the command line. Target metadata is ``ymer.db.Base.metadata``;
``ymer.models`` is imported when it exists so autogenerate sees every model.
"""

import importlib
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from ymer.config import get_settings
from ymer.db import Base

config = context.config
# Tests set configure_logger=False so running migrations does not reset pytest's logging.
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)

# Register all models on Base.metadata. ymer/models/__init__.py should import
# every model module.
try:
    importlib.import_module("ymer.models")
except ModuleNotFoundError as exc:
    if exc.name != "ymer.models":
        raise

target_metadata = Base.metadata


def _database_url() -> str:
    x_args = context.get_x_argument(as_dictionary=True)
    if "db_url" in x_args:
        return x_args["db_url"]
    settings = get_settings()
    return settings.test_database_url if settings.env == "test" else settings.database_url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
