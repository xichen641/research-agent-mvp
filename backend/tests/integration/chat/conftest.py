import asyncio
import selectors
import sys
from collections.abc import Callable, Mapping

from pytest import Config, Item


def pytest_asyncio_loop_factories(
    config: Config,
    item: Item,
) -> Mapping[str, Callable[[], asyncio.AbstractEventLoop]]:
    del config, item
    if sys.platform == "win32":
        return {
            "selector": lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())
        }
    return {"default": asyncio.new_event_loop}
