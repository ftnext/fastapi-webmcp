class FastAPIWebMCPError(RuntimeError):
    """Base error raised by fastapi-webmcp."""


class RouteConversionError(FastAPIWebMCPError):
    """A selected FastAPI route cannot be represented as a browser tool."""
