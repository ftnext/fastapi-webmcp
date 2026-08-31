# fastapi-webmcp

Expose selected FastAPI routes as tools on `document.modelContext` for browser
agents. The package generates tool schemas from FastAPI's OpenAPI document and
ships a small framework-free JavaScript bridge. It does not run an MCP server.

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
Required header and cookie parameters, external references, cyclic schemas,
multipart bodies, and form bodies are rejected instead of being exposed
incorrectly.

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
