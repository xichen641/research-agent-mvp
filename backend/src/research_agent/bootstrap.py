import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from research_agent.chat.graph import _checkpoint_database_url
from research_agent.config import get_settings


def main() -> None:
    """Apply application and LangGraph schemas before serving traffic."""

    settings = get_settings()
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", settings.database_url)
    command.upgrade(config, "head")
    asyncio.run(_setup_checkpoints(settings.database_url))


async def _setup_checkpoints(database_url: str) -> None:
    async with AsyncPostgresSaver.from_conn_string(
        _checkpoint_database_url(database_url)
    ) as saver:
        await saver.setup()


if __name__ == "__main__":
    main()
