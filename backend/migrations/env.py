from __future__ import annotations

import asyncio
from collections.abc import MutableMapping
from logging.config import fileConfig
from typing import Literal

from alembic import context
from sqlalchemy import Connection, pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from research_agent.chat import models as chat_models  # noqa: F401
from research_agent.config import get_settings
from research_agent.discovery import models as discovery_models  # noqa: F401
from research_agent.ingestion import models as ingestion_models  # noqa: F401
from research_agent.notes import models as note_models  # noqa: F401
from research_agent.papers import models as paper_models  # noqa: F401
from research_agent.retrieval import models as retrieval_models  # noqa: F401
from research_agent.shared.db import Base


config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
LANGGRAPH_CHECKPOINT_TABLES = frozenset(
    {
        "checkpoint_migrations",
        "checkpoints",
        "checkpoint_blobs",
        "checkpoint_writes",
    }
)


def include_name(
    name: str | None,
    type_: Literal[
        "schema",
        "table",
        "column",
        "index",
        "unique_constraint",
        "foreign_key_constraint",
    ],
    parent_names: MutableMapping[
        Literal["schema_name", "table_name", "schema_qualified_table_name"],
        str | None,
    ],
) -> bool:
    del parent_names
    # LangGraph owns these tables and migrates them through saver.setup().
    # LangGraph 通过 saver.setup() 自行管理这些表及其迁移。
    return not (type_ == "table" and name in LANGGRAPH_CHECKPOINT_TABLES)


def _database_url() -> str:
    configured_url = (config.get_main_option("sqlalchemy.url") or "").strip()
    return configured_url or get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_name=include_name,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        include_name=include_name,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _database_url()
    connectable = async_engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    injected_connection = config.attributes.get("connection")
    if injected_connection is not None:
        do_run_migrations(injected_connection)
    else:
        asyncio.run(run_async_migrations())
