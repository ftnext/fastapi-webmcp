from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TypeVar

from .models import ToolMetadata

F = TypeVar("F", bound=Callable[..., object])
METADATA_ATTRIBUTE = "__fastapi_webmcp_tool__"


def webmcp_tool(
    *,
    name: str | None = None,
    description: str | None = None,
    read_only: bool | None = None,
    untrusted_content: bool = True,
    headers: Mapping[str, str] | None = None,
) -> Callable[[F], F]:
    """Mark a FastAPI endpoint for exposure as an in-page WebMCP tool.

    Place this below the FastAPI route decorator so it is applied to the
    endpoint before FastAPI registers the function::

        @app.get("/items", operation_id="list_items")
        @webmcp_tool(read_only=True)
        async def list_items(): ...
    """

    metadata = ToolMetadata(
        name=name,
        description=description,
        read_only=read_only,
        untrusted_content=untrusted_content,
        header_params=tuple((headers or {}).items()),
    )

    def decorator(function: F) -> F:
        setattr(function, METADATA_ATTRIBUTE, metadata)
        return function

    return decorator


def metadata_for(function: Callable[..., object]) -> ToolMetadata | None:
    value = getattr(function, METADATA_ATTRIBUTE, None)
    return value if isinstance(value, ToolMetadata) else None
