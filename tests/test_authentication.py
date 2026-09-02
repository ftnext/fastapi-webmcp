from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response, Security
from fastapi.security import SecurityScopes
from fastapi.testclient import TestClient

from fastapi_webmcp import FastAPIWebMCP, WebMCPManifest, static_tool, webmcp_tool


@dataclass(frozen=True)
class User:
    name: str
    permissions: frozenset[str]


def authenticate_user(
    security_scopes: SecurityScopes,
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    users = {
        "Bearer reader": User("reader", frozenset({"documents:read"})),
        "Bearer editor": User(
            "editor",
            frozenset({"documents:read", "documents:write"}),
        ),
    }
    user = users.get(authorization or "")
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not set(security_scopes.scopes).issubset(user.permissions):
        raise HTTPException(status_code=403, detail="Not enough permissions")
    return user


def test_manifest_provider_uses_fastapi_security_and_scopes() -> None:
    app = FastAPI()
    webmcp = FastAPIWebMCP(app, credentials="same-origin")
    guide = static_tool(name="documents.read", description="Read documents.", text="Read")

    @app.post("/documents/current", operation_id="update_document")
    @webmcp_tool(name="documents.edit", description="Edit the current document.")
    async def update_document(
        user: Annotated[
            User,
            Security(authenticate_user, scopes=["documents:write"]),
        ],
    ) -> dict[str, str]:
        return {"updated_by": user.name}

    editor = webmcp.tools()[0]

    async def page_manifest(
        user: Annotated[
            User,
            Security(authenticate_user, scopes=["documents:read"]),
        ],
    ) -> WebMCPManifest:
        tools = [guide]
        if "documents:write" in user.permissions:
            tools.append(editor)
        return WebMCPManifest(
            tools=tools,
            context={"user": user.name, "canWrite": editor in tools},
        )

    webmcp.mount(page="/_webmcp", manifest_provider=page_manifest)
    client = TestClient(app)

    anonymous = client.get("/_webmcp/manifest.json")
    assert anonymous.status_code == 401
    assert anonymous.headers["www-authenticate"] == "Bearer"

    reader_headers = {"Authorization": "Bearer reader"}
    reader = client.get("/_webmcp/manifest.json", headers=reader_headers)
    assert reader.status_code == 200
    assert {tool["name"] for tool in reader.json()["tools"]} == {"documents.read"}
    assert reader.json()["context"] == {"user": "reader", "canWrite": False}
    assert client.post("/documents/current", headers=reader_headers).status_code == 403

    editor_headers = {"Authorization": "Bearer editor"}
    editable = client.get("/_webmcp/manifest.json", headers=editor_headers)
    assert editable.status_code == 200
    assert {tool["name"] for tool in editable.json()["tools"]} == {
        "documents.read",
        "documents.edit",
    }
    assert editable.json()["context"] == {"user": "editor", "canWrite": True}
    assert client.post("/documents/current", headers=editor_headers).json() == {
        "updated_by": "editor"
    }


def test_mount_dependencies_can_protect_a_static_manifest() -> None:
    app = FastAPI()
    webmcp = FastAPIWebMCP(app)

    def require_session(
        session: Annotated[str | None, Header(alias="X-Session")] = None,
    ) -> None:
        if session != "valid":
            raise HTTPException(status_code=401, detail="Invalid session")

    webmcp.mount(page="/_webmcp", dependencies=[Depends(require_session)])
    client = TestClient(app)

    assert client.get("/_webmcp/manifest.json").status_code == 401
    authenticated = client.get(
        "/_webmcp/manifest.json",
        headers={"X-Session": "valid"},
    )
    assert authenticated.status_code == 200

    # Runtime files contain no user data and remain public by design.
    assert client.get("/_webmcp/runtime.js").status_code == 200


def test_manifest_provider_honors_dependency_overrides_and_security_scopes() -> None:
    app = FastAPI()
    webmcp = FastAPIWebMCP(app)
    captured_scopes: list[str] = []

    async def page_manifest(
        user: Annotated[
            User,
            Security(authenticate_user, scopes=["documents:read"]),
        ],
    ) -> WebMCPManifest:
        return WebMCPManifest(
            tools=[],
            context={"user": user.name},
        )

    def override_user(security_scopes: SecurityScopes) -> User:
        captured_scopes.extend(security_scopes.scopes)
        return User("test-user", frozenset(security_scopes.scopes))

    app.dependency_overrides[authenticate_user] = override_user
    webmcp.mount(page="/_webmcp", manifest_provider=page_manifest)

    response = TestClient(app).get("/_webmcp/manifest.json")

    assert response.status_code == 200
    assert response.json()["context"] == {"user": "test-user"}
    assert captured_scopes == ["documents:read"]


def test_manifest_provider_closes_yield_dependencies() -> None:
    app = FastAPI()
    webmcp = FastAPIWebMCP(app)
    events: list[str] = []

    async def request_resource() -> AsyncIterator[str]:
        events.append("enter")
        try:
            yield "resource"
        finally:
            events.append("exit")

    async def page_manifest(
        resource: Annotated[str, Depends(request_resource)],
    ) -> WebMCPManifest:
        events.append(resource)
        return WebMCPManifest(tools=[])

    webmcp.mount(page="/_webmcp", manifest_provider=page_manifest)

    response = TestClient(app).get("/_webmcp/manifest.json")

    assert response.status_code == 200
    assert events == ["enter", "resource", "exit"]


def test_manifest_provider_uses_fastapi_request_validation() -> None:
    app = FastAPI()
    webmcp = FastAPIWebMCP(app)

    async def page_manifest(
        slug: Annotated[str, Query(min_length=3)],
    ) -> WebMCPManifest:
        return WebMCPManifest(tools=[], context={"slug": slug})

    webmcp.mount(page="/_webmcp", manifest_provider=page_manifest)
    client = TestClient(app)

    missing = client.get("/_webmcp/manifest.json")
    assert missing.status_code == 422
    assert missing.json()["detail"][0]["loc"] == ["query", "slug"]

    valid = client.get("/_webmcp/manifest.json?slug=demo")
    assert valid.status_code == 200
    assert valid.json()["context"] == {"slug": "demo"}


def test_manifest_provider_none_fails_closed() -> None:
    app = FastAPI()

    @app.get("/admin", operation_id="admin")
    @webmcp_tool()
    async def admin() -> dict[str, bool]:
        return {"ok": True}

    async def broken_provider() -> None:
        return None

    webmcp = FastAPIWebMCP(app)
    webmcp.mount(
        page="/_webmcp",
        manifest_provider=broken_provider,  # type: ignore[arg-type]
    )

    response = TestClient(app, raise_server_exceptions=False).get("/_webmcp/manifest.json")

    assert response.status_code == 500
    assert response.text == "Internal Server Error"


def test_manifest_preserves_response_changes_from_auth_dependencies() -> None:
    app = FastAPI()
    webmcp = FastAPIWebMCP(app)

    def rotate_session(response: Response) -> None:
        response.status_code = 202
        response.headers["X-Auth"] = "rotated"
        response.set_cookie("session", "new-session", httponly=True)

    async def page_manifest(
        _: Annotated[None, Depends(rotate_session)],
    ) -> WebMCPManifest:
        return WebMCPManifest(tools=[])

    webmcp.mount(page="/_webmcp", manifest_provider=page_manifest)

    response = TestClient(app).get("/_webmcp/manifest.json")

    assert response.status_code == 202
    assert response.headers["X-Auth"] == "rotated"
    assert response.cookies["session"] == "new-session"
    assert response.headers["Cache-Control"] == "no-store"
