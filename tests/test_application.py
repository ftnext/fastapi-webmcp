from typing import Annotated

import pytest
from fastapi import FastAPI, Path, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from fastapi_webmcp import FastAPIWebMCP, FastAPIWebMCPError, webmcp_tool


class ItemCreate(BaseModel):
    name: str = Field(description="Human-readable item name")
    quantity: int = Field(ge=1)


def build_app() -> tuple[FastAPI, FastAPIWebMCP]:
    app = FastAPI()
    webmcp = FastAPIWebMCP(app)

    @app.get("/health", operation_id="health")
    async def health() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/items", operation_id="list_items")
    @webmcp_tool(read_only=True, untrusted_content=True)
    async def list_items(limit: Annotated[int, Query(ge=1)] = 20) -> list[dict[str, object]]:
        return [{"id": "one", "limit": limit}]

    @app.post("/groups/{group_id}/items", operation_id="create_item")
    @webmcp_tool(
        name="inventory.create_item",
        description="Create an item in an inventory group.",
        untrusted_content=False,
    )
    async def create_item(
        group_id: Annotated[str, Path(description="Inventory group id")],
        item: ItemCreate,
        notify: bool = False,
    ) -> dict[str, object]:
        return {"group_id": group_id, **item.model_dump(), "notify": notify}

    webmcp.mount(page="/agent")
    return app, webmcp


def test_mount_serves_page_javascript_and_manifest() -> None:
    app, _ = build_app()
    client = TestClient(app)

    page = client.get("/agent")
    assert page.status_code == 200
    assert "FastAPI WebMCP" in page.text
    assert '<script type="module" src="./bridge.js"></script>' in page.text

    javascript = client.get("/agent/bridge.js")
    assert javascript.status_code == 200
    assert "document.modelContext" in javascript.text
    assert "context.registerTool" in javascript.text

    manifest_response = client.get("/agent/manifest.json")
    assert manifest_response.status_code == 200
    assert manifest_response.headers["cache-control"] == "no-store"
    manifest = manifest_response.json()
    assert manifest["version"] == 1
    assert manifest["credentials"] == "omit"
    assert {tool["name"] for tool in manifest["tools"]} == {
        "inventory.create_item",
        "list_items",
    }


def test_openapi_contract_becomes_request_mapping_and_input_schema() -> None:
    _, webmcp = build_app()
    tools = {tool.name: tool.as_dict() for tool in webmcp.tools()}

    listing = tools["list_items"]
    assert listing["request"] == {
        "method": "GET",
        "path": "/items",
        "pathParams": [],
        "queryParams": ["limit"],
        "bodyParams": [],
    }
    assert listing["inputSchema"]["properties"]["limit"]["minimum"] == 1
    assert listing["annotations"] == {
        "readOnlyHint": True,
        "untrustedContentHint": True,
    }

    creation = tools["inventory.create_item"]
    assert creation["description"] == "Create an item in an inventory group."
    assert creation["request"] == {
        "method": "POST",
        "path": "/groups/{group_id}/items",
        "pathParams": ["group_id"],
        "queryParams": ["notify"],
        "bodyParams": ["name", "quantity"],
    }
    assert set(creation["inputSchema"]["properties"]) == {
        "group_id",
        "notify",
        "name",
        "quantity",
    }
    assert set(creation["inputSchema"]["required"]) == {
        "group_id",
        "name",
        "quantity",
    }
    assert creation["annotations"] == {
        "readOnlyHint": False,
        "untrustedContentHint": False,
    }


def test_undecorated_routes_are_not_exposed_by_default() -> None:
    _, webmcp = build_app()
    assert "health" not in {tool.name for tool in webmcp.tools()}


def test_include_operations_bootstraps_an_existing_app() -> None:
    app = FastAPI()

    @app.get("/health", operation_id="health")
    async def health() -> dict[str, bool]:
        return {"ok": True}

    webmcp = FastAPIWebMCP.from_fastapi(app, include_operations={"health"})
    assert [tool.name for tool in webmcp.tools()] == ["health"]


def test_support_routes_do_not_appear_in_openapi() -> None:
    app, _ = build_app()
    paths = TestClient(app).get("/openapi.json").json()["paths"]
    assert "/agent" not in paths
    assert "/agent/manifest.json" not in paths


def test_mount_is_single_use_and_rejects_the_application_root() -> None:
    app = FastAPI()
    webmcp = FastAPIWebMCP(app)

    with pytest.raises(FastAPIWebMCPError, match="cannot replace"):
        webmcp.mount(page="/")

    webmcp.mount()
    with pytest.raises(FastAPIWebMCPError, match="already mounted"):
        webmcp.mount(page="/other")


def test_manifest_normalizes_an_asgi_root_path() -> None:
    _, webmcp = build_app()
    assert webmcp.manifest(root_path="/proxy/service/")["basePath"] == "/proxy/service"


def test_credentials_must_be_an_explicit_supported_value() -> None:
    with pytest.raises(FastAPIWebMCPError, match="credentials"):
        FastAPIWebMCP(FastAPI(), credentials="include")  # type: ignore[arg-type]
