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

A runnable version is included in [`examples/basic.py`](examples/basic.py):

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
the tool to another document.

```python
from fastapi_webmcp import RequestTool, WebMCPManifest


async def page_manifest(request: Request) -> WebMCPManifest:
    slug = request.query_params["slug"]
    can_write = await viewer_can_write(request, slug)
    tools = [tool.bind_path(slug=slug) for tool in webmcp.tools() if isinstance(tool, RequestTool)]
    if can_write:
        tools.append(replace_content_tool)
    return WebMCPManifest(tools=tools, context={"slug": slug, "canWrite": can_write})


webmcp.mount(page="/_webmcp", manifest_provider=page_manifest)
```

The provider is evaluated for every manifest request. Applications should
still re-check write permission inside a client handler and at the persistence
boundary.

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

The same function can be wrapped in a small React hook; the Python package has
no React dependency.

A complete framework-free example is included in
[`examples/thinkroom_lite.py`](examples/thinkroom_lite.py):

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
uv build
```
