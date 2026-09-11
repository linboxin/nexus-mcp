from __future__ import annotations

import functools
from typing import Any, Awaitable, Callable, Iterable, TypeVar

from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel

from ..errors import NexusError

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True)

F = TypeVar("F", bound=Callable[..., Awaitable[Any]])


def nexus_tool(fn: F) -> F:
    """Translate NexusError / ValueError into MCP tool errors that keep the NEXUS_* code."""

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await fn(*args, **kwargs)
        except NexusError as exc:
            raise ToolError(f"{exc.code}: {exc.message}") from exc
        except ValueError as exc:
            raise ToolError(f"NEXUS_INVALID_ARGUMENT: {exc}") from exc

    return wrapper  # type: ignore[return-value]


def dump(items: Iterable[BaseModel]) -> list[dict[str, Any]]:
    return [item.model_dump(mode="json") for item in items]
