from __future__ import annotations

from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from importlib.resources import files
from typing import Any, TypeAlias

from fastapi import Depends, FastAPI, Request, Response, params

from .discovery import AUTHENTICATION_HEADERS, TOOL_NAME_PATTERN, ToolDiscovery
from .exceptions import FastAPIWebMCPError, RouteConversionError
from .models import (
    BrowserTool,
    ClientTool,
    RequestTool,
    StaticTool,
    ToolCredentials,
    WebMCPManifest,
)

ManifestProviderResult: TypeAlias = WebMCPManifest | Collection[BrowserTool]
ManifestProvider: TypeAlias = Callable[
    ..., ManifestProviderResult | Awaitable[ManifestProviderResult]
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
        dependencies: Sequence[params.Depends] = (),
    ) -> None:
        """Mount the packaged page, runtime, and a possibly protected dynamic manifest."""

        if self._page_path is not None:
            raise FastAPIWebMCPError(f"fastapi-webmcp is already mounted at {self._page_path}")
        if not hasattr(self.app, "frontend"):
            raise FastAPIWebMCPError("FastAPI.app.frontend() is required; install fastapi>=0.141.1")

        page_path = self._normalize_page_path(page)
        manifest_path = f"{page_path}/manifest.json"

        def manifest_payload(
            request: Request,
            page_manifest: WebMCPManifest | None = None,
        ) -> dict[str, Any]:
            return self.manifest(
                root_path=request.scope.get("root_path", ""),
                tools=page_manifest.tools if page_manifest is not None else None,
                context=page_manifest.context if page_manifest is not None else None,
            )

        manifest_endpoint: Callable[..., Awaitable[dict[str, Any]]]
        if manifest_provider is None:

            async def serve_default_manifest(
                request: Request,
                response: Response,
            ) -> dict[str, Any]:
                response.headers["Cache-Control"] = "no-store"
                return manifest_payload(request)

            manifest_endpoint = serve_default_manifest
        else:
            provider = manifest_provider
            provider_dependency: Any = Depends(provider)

            async def serve_dynamic_manifest(
                request: Request,
                response: Response,
                provided: ManifestProviderResult = provider_dependency,
            ) -> dict[str, Any]:
                if provided is None:
                    raise FastAPIWebMCPError("manifest provider returned None")
                response.headers["Cache-Control"] = "no-store"
                return manifest_payload(request, self._page_manifest(provided))

            manifest_endpoint = serve_dynamic_manifest

        self.app.add_api_route(
            manifest_path,
            manifest_endpoint,
            methods=["GET"],
            dependencies=list(dependencies),
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

    def _page_manifest(
        self,
        provided: ManifestProviderResult,
    ) -> WebMCPManifest:
        if isinstance(provided, WebMCPManifest):
            return provided
        return WebMCPManifest(tools=provided)

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
            if isinstance(tool, RequestTool):
                authentication_headers = sorted(
                    header_name
                    for _, header_name in tool.request.header_params
                    if header_name.casefold() in AUTHENTICATION_HEADERS
                )
                if authentication_headers:
                    header_names = ", ".join(authentication_headers)
                    raise RouteConversionError(
                        f"tool {tool.name!r} cannot expose authentication headers "
                        f"as tool inputs: {header_names}"
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
