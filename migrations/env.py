from logging.config import fileConfig

from alembic import context
from pgvector.sqlalchemy import Vector
from sqlalchemy import create_engine, pool

import dr_core.db.models  # noqa: F401
from dr_core.config import get_settings
from dr_core.db.base import Base
from dr_core.db.session import sqlalchemy_url

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _database_url() -> str:
    return sqlalchemy_url(get_settings().database_url)


def _render_item(type_, obj, autogen_context):
    if type_ == "type" and isinstance(obj, Vector):
        autogen_context.imports.add("from pgvector.sqlalchemy import Vector")
        return f"Vector(dim={obj.dim})"
    return False


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        render_item=_render_item,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(
        _database_url(),
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_item=_render_item,
        )
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
