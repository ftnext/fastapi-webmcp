from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

ToolCredentials = Literal["omit", "same-origin"]


@dataclass(frozen=True, slots=True)
class ToolMetadata:
    name: str | None = None
    description: str | None = None
    read_only: bool | None = None
    untrusted_content: bool = True


@dataclass(frozen=True, slots=True)
class RequestMapping:
    method: str
    path: str
    path_params: tuple[str, ...]
    query_params: tuple[str, ...]
    body_params: tuple[str, ...]
    body_value_param: str | None = None

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "method": self.method,
            "path": self.path,
            "pathParams": list(self.path_params),
            "queryParams": list(self.query_params),
            "bodyParams": list(self.body_params),
        }
        if self.body_value_param is not None:
            result["bodyValueParam"] = self.body_value_param
        return result


@dataclass(frozen=True, slots=True)
class BrowserTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    read_only: bool
    untrusted_content: bool
    request: RequestMapping

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
            "annotations": {
                "readOnlyHint": self.read_only,
                "untrustedContentHint": self.untrusted_content,
            },
            "request": self.request.as_dict(),
        }
