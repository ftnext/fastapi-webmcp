from __future__ import annotations

from collections.abc import Collection
from importlib.resources import files
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .discovery import ToolDiscovery
from .exceptions import FastAPIWebMCPError
from .models import BrowserTool, ToolCredentials


class FastAPIWebMCP:
    """Expose selected FastAPI routes as tools registered by a browser page."""

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

    def tools(self) -> list[BrowserTool]:
        return self.discovery.discover()

    def manifest(self, *, root_path: str = "") -> dict[str, Any]:
        return {
            "version": 1,
            "basePath": self._normalize_root_path(root_path),
            "credentials": self.credentials,
            "tools": [tool.as_dict() for tool in self.tools()],
        }

    def mount(self, *, page: str = "/webmcp") -> None:
        if self._page_path is not None:
            raise FastAPIWebMCPError(f"fastapi-webmcp is already mounted at {self._page_path}")
        if not hasattr(self.app, "frontend"):
            raise FastAPIWebMCPError("FastAPI.app.frontend() is required; install fastapi>=0.141.1")

        page_path = self._normalize_page_path(page)
        manifest_path = f"{page_path}/manifest.json"

        async def serve_manifest(request: Request) -> JSONResponse:
            return JSONResponse(
                self.manifest(root_path=request.scope.get("root_path", "")),
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

    def _normalize_page_path(self, page: str) -> str:
        normalized = "/" + page.strip("/")
        if normalized == "/":
            raise FastAPIWebMCPError("the WebMCP page cannot replace the application root")
        return normalized

    def _normalize_root_path(self, root_path: str) -> str:
        stripped = root_path.strip("/")
        return f"/{stripped}" if stripped else ""
