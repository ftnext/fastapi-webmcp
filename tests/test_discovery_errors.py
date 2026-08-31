import pytest
from fastapi import FastAPI, Header

from fastapi_webmcp import FastAPIWebMCP, RouteConversionError, webmcp_tool


def test_duplicate_tool_names_are_rejected() -> None:
    app = FastAPI()

    @app.get("/one", operation_id="one")
    @webmcp_tool(name="duplicate")
    async def one() -> None:
        return None

    @app.get("/two", operation_id="two")
    @webmcp_tool(name="duplicate")
    async def two() -> None:
        return None

    with pytest.raises(RouteConversionError, match="duplicate"):
        FastAPIWebMCP(app).tools()


def test_required_header_parameters_are_rejected() -> None:
    app = FastAPI()

    @app.get("/protected", operation_id="protected")
    @webmcp_tool()
    async def protected(x_token: str = Header()) -> None:
        del x_token

    with pytest.raises(RouteConversionError, match="unsupported header"):
        FastAPIWebMCP(app).tools()


def test_invalid_tool_name_is_rejected() -> None:
    app = FastAPI()

    @app.get("/bad", operation_id="bad")
    @webmcp_tool(name="bad tool name")
    async def bad() -> None:
        return None

    with pytest.raises(RouteConversionError, match="must match"):
        FastAPIWebMCP(app).tools()
