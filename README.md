# fastapi-webmcp

Expose selected FastAPI routes as tools on `document.modelContext` for browser
agents. The package generates tool schemas from FastAPI's OpenAPI document and
ships a small framework-free JavaScript bridge. It does not run an MCP server.

Use it at two levels:

- add `@webmcp_tool` to ordinary FastAPI endpoints and use the packaged page;
- load the framework-neutral runtime from an existing HTML, React, Vue, or
  other frontend and provide handlers for page-local client tools.

## Install

```bash
uv add fastapi-webmcp
```

`fastapi-webmcp` requires FastAPI 0.141.1 or newer for `app.frontend()`.

## Quick start

```python
from fastapi import FastAPI
from pydantic import BaseModel

from fastapi_webmcp import FastAPIWebMCP, webmcp_tool

app = FastAPI()
webmcp = FastAPIWebMCP(app)


class ItemCreate(BaseModel):
    name: str
    quantity: int


@app.get("/items", operation_id="list_items")
@webmcp_tool(read_only=True)
async def list_items():
    return []


@app.post("/items", operation_id="create_item")
@webmcp_tool(description="Create an item.", untrusted_content=False)
async def create_item(item: ItemCreate):
    return item


webmcp.mount(page="/agent")
```

Run the FastAPI application and open `/agent`. A supporting browser registers
`list_items` and `create_item`; other routes stay private.

The decorator belongs directly below the FastAPI route decorator. Explicit
`operation_id` values produce stable tool names.

A runnable version is included in
[`examples/basic.py`](https://github.com/ftnext/fastapi-webmcp/blob/main/examples/basic.py):

```bash
uv run uvicorn examples.basic:app --reload
```

## Tool kinds

The manifest supports three kinds of tools:

- `request`: generated from a decorated FastAPI operation and executed as an
  HTTP request;
- `static`: returns fixed guidance without a request;
- `client`: dispatches to a handler explicitly supplied by the current page.

Static and client tools can join a default manifest:

```python
from fastapi_webmcp import client_tool, static_tool

webmcp.add_tool(
    static_tool(
        name="room.guide",
        description="Explain how to use this room.",
        text="Read the document before editing it.",
    )
)

webmcp.add_tool(
    client_tool(
        name="room.replace_content",
        action="replace_content",
        description="Replace the content in the current editor.",
        input_schema={
            "type": "object",
            "properties": {"content": {"type": "string"}},
            "required": ["content"],
            "additionalProperties": False,
        },
    )
)
```

The manifest contains only the client action name. It never contains
executable JavaScript.

## Explicit header inputs

A decorator can expose an OpenAPI header parameter under a tool-friendly input
name. Unmapped required headers remain an error.

```python
@app.post("/documents/{slug}/comments")
@webmcp_tool(
    name="room.comment",
    headers={"agent_name": "X-Agent-Name"},
)
async def comment(
    slug: str,
    comment: CommentCreate,
    agent_name: Annotated[str, Header(alias="X-Agent-Name")],
): ...
```

`agent_name` appears in the tool schema but is sent as `X-Agent-Name`, not in
the JSON body.

## Dynamic page manifests

Use `manifest_provider` when the tool set depends on a document, user, or page
capability. A page-scoped path value can be bound so the agent cannot retarget
the tool to another document. The provider is resolved by FastAPI's dependency
system, so it can use `Depends`, `Security`, request validation, dependency
overrides, and yield dependencies in the same way as a path operation.

```python
from typing import Annotated

from fastapi import Depends
from fastapi_webmcp import RequestTool, WebMCPManifest


async def page_manifest(
    request: Request,
    user: Annotated[User, Depends(current_user)],
) -> WebMCPManifest:
    slug = request.query_params["slug"]
    can_write = await user_can_write(user, slug)
    tools = [tool.bind_path(slug=slug) for tool in webmcp.tools() if isinstance(tool, RequestTool)]
    if can_write:
        tools.append(replace_content_tool)
    return WebMCPManifest(tools=tools, context={"slug": slug, "canWrite": can_write})


webmcp.mount(page="/_webmcp", manifest_provider=page_manifest)
```

The provider is evaluated for every manifest request. Applications should
still re-check write permission at every persistence boundary. Hiding a tool
from the manifest is a user-experience measure, not an authorization boundary.
A provider that accidentally returns `None` fails closed instead of falling
back to the default tool set.

### Authentication and authorization

Use the same FastAPI dependencies for the manifest and the underlying API
operations. `Security` scopes and `app.dependency_overrides` are preserved:

```python
from typing import Annotated

from fastapi import Security


async def page_manifest(
    user: Annotated[
        User,
        Security(current_user, scopes=["documents:read"]),
    ],
) -> WebMCPManifest:
    tools = readable_tools(user)
    if user.has_permission("documents:write"):
        tools.append(replace_content_tool)
    return WebMCPManifest(tools=tools, context={"canWrite": user.can_write})


@app.patch("/documents/{slug}")
async def update_document(
    slug: str,
    update: DocumentUpdate,
    user: Annotated[
        User,
        Security(current_user, scopes=["documents:write"]),
    ],
): ...
```

For a static manifest, or for checks whose return value is not needed by the
provider, pass parameterless dependencies just as you would to a FastAPI path
operation:

```python
webmcp.mount(
    page="/_webmcp",
    dependencies=[Depends(current_user)],
)
```

These dependencies protect `manifest.json`. The packaged HTML and JavaScript
remain public because they contain no user data or credentials. Protect a
separate application frontend with its own FastAPI router dependencies when
needed.

Required `Authorization` header dependencies and cookie dependencies are
transport-managed and do not become agent inputs. Bearer headers must be
supplied with `requestHeaders`; cookies require `credentials="same-origin"`.

The runtime always loads the same-origin manifest with browser credentials.
For cookie sessions, select `credentials="same-origin"` so request tools also
send the session cookie; enable CSRF protection for state-changing operations:

```python
webmcp = FastAPIWebMCP(app, credentials="same-origin")
```

An existing frontend that uses bearer tokens can supply headers at request
time without placing secrets in the manifest:

```javascript
registerWebMCP({
  manifestUrl: "/_webmcp/manifest.json",
  requestHeaders: () => ({ Authorization: `Bearer ${readAccessToken()}` }),
})
```

`requestHeaders` is used only for same-origin manifest and tool requests. It
can be a headers object or an async callback receiving `{ kind, url, tool,
signal }`. The application-provided headers override agent-controlled header
inputs when names collide. Authentication headers such as `Authorization` and
`Cookie` cannot be exposed as agent-controlled `@webmcp_tool` inputs.

Do not authenticate the same request as different users through a session
cookie and a bearer token. Applications that support both should reject
ambiguous requests or define one unambiguous credential source.

## Existing frontend integration

`mount()` serves `runtime.js` as an ES module. An existing frontend imports it
and supplies only the client actions it owns:

```javascript
import { registerWebMCP } from "/_webmcp/runtime.js"

const registration = registerWebMCP({
  manifestUrl: `/_webmcp/manifest.json?slug=${slug}`,
  handlers: {
    replace_content: async ({ content }, { signal, context }) => {
      const previousContent = editor.getValue()
      editor.replaceContent(content)
      await saveDocument({ content, signal })
      return { ok: true, previous_content: previousContent, context }
    },
  },
})

registration.ready.catch(console.error)
// Call registration.abort() from React useEffect cleanup, Vue onUnmounted,
// or a pagehide listener.
```

After login, logout, account switching, or a permission change, refresh the
registration. The runtime fetches and authorizes the next manifest before it
aborts the previous tool generation; a partially registered new generation is
aborted as a group:

```javascript
await registration.refresh()
```

Request tools are always authorized again by their FastAPI endpoints. Client
tool handlers must also check current page authorization before changing UI
state, and persistence endpoints must independently authorize every write.
Tool and manifest fetches reject redirects so authentication credentials and
request bodies cannot be redirected outside the generated application path.

The same function can be wrapped in a small React hook; the Python package has
no React dependency.

A complete framework-free example is included in
[`examples/thinkroom_lite.py`](https://github.com/ftnext/fastapi-webmcp/blob/main/examples/thinkroom_lite.py):

```bash
uv run uvicorn examples.thinkroom_lite:app --reload
# open http://127.0.0.1:8000/room?slug=demo&mode=edit
```

## Adopt an existing application

Routes can be selected without decorators while prototyping:

```python
webmcp = FastAPIWebMCP.from_fastapi(
    app,
    include_operations={"list_items", "create_item"},
)
webmcp.mount(page="/agent")
```

Selection by OpenAPI tag is also available through `include_tags`. Automatic
exposure of every OpenAPI route requires the explicit `expose_all=True` flag.

## Request mapping

The generated browser bridge supports:

- path parameters;
- scalar and repeated query parameters;
- JSON object request bodies;
- JSON scalar and array request bodies;
- GET, POST, PUT, PATCH, and DELETE;
- cancellation through `AbortSignal`;
- same-origin URL enforcement;
- MCP-style text and error result envelopes.

Local OpenAPI `$ref` schemas are dereferenced before they reach the browser.
Required headers without an explicit `headers={...}` mapping, cookie
parameters, external references, cyclic schemas, multipart bodies, and form
bodies are rejected instead of being exposed incorrectly.

## Security defaults

Only decorated or explicitly selected operations are exposed. Tool calls use
`credentials: "omit"` by default, so the browser's account cookies do not
silently authorize an agent. `credentials="same-origin"` is available as an
explicit opt-in, but applications are responsible for CSRF protection and
correct agent attribution.

The manifest is loaded with same-origin credentials so application-level
dependencies can protect the page and manifest. It contains no credentials or
authorization headers.

## Development

```bash
uv sync
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
node --test tests/runtime.test.mjs
uv build
```
