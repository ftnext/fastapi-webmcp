from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Collection, Mapping
from importlib.resources import files
from typing import Any, TypeAlias

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .discovery import TOOL_NAME_PATTERN, ToolDiscovery
from .exceptions import FastAPIWebMCPError, RouteConversionError
from .models import (
    BrowserTool,
    ClientTool,
    StaticTool,
    ToolCredentials,
    WebMCPManifest,
)

ManifestProviderResult: TypeAlias = WebMCPManifest | Collection[BrowserTool]
ManifestProvider: TypeAlias = Callable[
    [Request], ManifestProviderResult | Awaitable[ManifestProviderResult]
]


class FastAPIWebMCP:
    """Expose selected FastAPI routes and page tools to browser agents."""

    def __init__(
        self,
        app: FastAPI,
        *,
        include_operations: Collection[str] = (),
        include_tags: Collection[str] = (),
        exclude_operations: Collection[str] = (),
        expose_all: bool = False,
        credentials: ToolCredentials = "omit",
    ) -> None:
        if credentials not in {"omit", "same-origin"}:
            raise FastAPIWebMCPError("credentials must be either 'omit' or 'same-origin'")
        self.app = app
        self.credentials = credentials
        self.discovery = ToolDiscovery(
            app,
            include_operations=include_operations,
            include_tags=include_tags,
            exclude_operations=exclude_operations,
            expose_all=expose_all,
        )
        self._additional_tools: list[BrowserTool] = []
        self._page_path: str | None = None

    @classmethod
    def from_fastapi(
        cls,
        app: FastAPI,
        *,
        include_operations: Collection[str] = (),
        include_tags: Collection[str] = (),
        exclude_operations: Collection[str] = (),
        expose_all: bool = False,
        credentials: ToolCredentials = "omit",
    ) -> FastAPIWebMCP:
        return cls(
            app,
            include_operations=include_operations,
            include_tags=include_tags,
            exclude_operations=exclude_operations,
            expose_all=expose_all,
            credentials=credentials,
        )

    def add_tool(self, tool: BrowserTool) -> BrowserTool:
        """Add a static or client tool to every default manifest."""

        candidate = [*self._additional_tools, tool]
        self._validate_tools(candidate)
        self._additional_tools.append(tool)
        return tool

    def tools(self) -> list[BrowserTool]:
        tools: list[BrowserTool] = [*self.discovery.discover(), *self._additional_tools]
        self._validate_tools(tools)
        return tools

    def manifest(
        self,
        *,
        root_path: str = "",
        tools: Collection[BrowserTool] | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        selected = list(self.tools() if tools is None else tools)
        self._validate_tools(selected)
        result: dict[str, Any] = {
            "version": 1,
            "basePath": self._normalize_root_path(root_path),
            "credentials": self.credentials,
            "tools": [tool.as_dict() for tool in selected],
        }
        if context:
            result["context"] = dict(context)
        return result

    def mount(
        self,
        *,
        page: str = "/webmcp",
        manifest_provider: ManifestProvider | None = None,
    ) -> None:
        """Mount the packaged page, runtime, and a possibly dynamic manifest."""

        if self._page_path is not None:
            raise FastAPIWebMCPError(f"fastapi-webmcp is already mounted at {self._page_path}")
        if not hasattr(self.app, "frontend"):
            raise FastAPIWebMCPError("FastAPI.app.frontend() is required; install fastapi>=0.141.1")

        page_path = self._normalize_page_path(page)
        manifest_path = f"{page_path}/manifest.json"

        async def serve_manifest(request: Request) -> JSONResponse:
            page_manifest: WebMCPManifest | None = None
            if manifest_provider is not None:
                provided = manifest_provider(request)
                if inspect.isawaitable(provided):
                    provided = await provided
                page_manifest = (
                    provided
                    if isinstance(provided, WebMCPManifest)
                    else WebMCPManifest(tools=provided)
                )
            return JSONResponse(
                self.manifest(
                    root_path=request.scope.get("root_path", ""),
                    tools=page_manifest.tools if page_manifest is not None else None,
                    context=page_manifest.context if page_manifest is not None else None,
                ),
                headers={"Cache-Control": "no-store"},
            )

        self.app.add_api_route(
            manifest_path,
            serve_manifest,
            methods=["GET"],
            include_in_schema=False,
            name="fastapi_webmcp_manifest",
        )
        static_directory = files("fastapi_webmcp").joinpath("static")
        self.app.frontend(
            page_path,
            directory=str(static_directory),
            fallback="index.html",
        )
        self._page_path = page_path

    def _validate_tools(self, tools: Collection[BrowserTool]) -> None:
        names: set[str] = set()
        for tool in tools:
            if not TOOL_NAME_PATTERN.fullmatch(tool.name):
                raise RouteConversionError(
                    f"tool name {tool.name!r} must match {TOOL_NAME_PATTERN.pattern}"
                )
            if tool.name in names:
                raise RouteConversionError(f"duplicate WebMCP tool name: {tool.name}")
            names.add(tool.name)
            schema = tool.input_schema
            if schema.get("type") != "object":
                raise RouteConversionError(f"tool {tool.name!r} input schema must be an object")
            properties = schema.get("properties")
            required = schema.get("required")
            if not isinstance(properties, Mapping) or not isinstance(required, list):
                raise RouteConversionError(
                    f"tool {tool.name!r} input schema needs properties and required"
                )
            unknown_required = [
                name for name in required if not isinstance(name, str) or name not in properties
            ]
            if unknown_required:
                required_names = ", ".join(sorted(str(name) for name in unknown_required))
                raise RouteConversionError(
                    f"tool {tool.name!r} requires unknown input properties: {required_names}"
                )
            if isinstance(tool, ClientTool) and not TOOL_NAME_PATTERN.fullmatch(tool.action):
                raise RouteConversionError(
                    f"client action {tool.action!r} must match {TOOL_NAME_PATTERN.pattern}"
                )
            if isinstance(tool, StaticTool) and not isinstance(tool.text, str):
                raise RouteConversionError(f"static tool {tool.name!r} text must be a string")

    def _normalize_page_path(self, page: str) -> str:
        normalized = "/" + page.strip("/")
        if normalized == "/":
            raise FastAPIWebMCPError("the WebMCP page cannot replace the application root")
        return normalized

    def _normalize_root_path(self, root_path: str) -> str:
        stripped = root_path.strip("/")
        return f"/{stripped}" if stripped else ""
