from __future__ import annotations

from collections.abc import Collection, Mapping
from copy import deepcopy
from dataclasses import dataclass, field, replace
from typing import Any, Literal

ToolCredentials = Literal["omit", "same-origin"]


def empty_input_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    }


@dataclass(frozen=True, slots=True)
class ToolMetadata:
    name: str | None = None
    description: str | None = None
    read_only: bool | None = None
    untrusted_content: bool = True
    header_params: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class RequestMapping:
    method: str
    path: str
    path_params: tuple[str, ...]
    query_params: tuple[str, ...]
    body_params: tuple[str, ...]
    body_value_param: str | None = None
    header_params: tuple[tuple[str, str], ...] = ()
    bound_path_params: tuple[tuple[str, Any], ...] = ()

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
        if self.header_params:
            result["headerParams"] = dict(self.header_params)
        if self.bound_path_params:
            result["boundPathParams"] = dict(self.bound_path_params)
        return result


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]
    read_only: bool
    untrusted_content: bool

    def base_dict(self, *, kind: str) -> dict[str, Any]:
        return {
            "kind": kind,
            "name": self.name,
            "description": self.description,
            "inputSchema": deepcopy(self.input_schema),
            "annotations": {
                "readOnlyHint": self.read_only,
                "untrustedContentHint": self.untrusted_content,
            },
        }


@dataclass(frozen=True, slots=True)
class RequestTool(ToolDefinition):
    request: RequestMapping

    def as_dict(self) -> dict[str, Any]:
        return {**self.base_dict(kind="request"), "request": self.request.as_dict()}

    def bind_path(self, **values: Any) -> RequestTool:
        """Bind page-scoped path inputs and remove them from the agent schema."""

        unknown = set(values) - set(self.request.path_params)
        if unknown:
            names = ", ".join(sorted(unknown))
            raise ValueError(f"cannot bind unknown path parameters: {names}")

        schema = deepcopy(self.input_schema)
        properties = schema.get("properties")
        if isinstance(properties, dict):
            for name in values:
                properties.pop(name, None)
        required = schema.get("required")
        if isinstance(required, list):
            schema["required"] = [name for name in required if name not in values]

        remaining = tuple(name for name in self.request.path_params if name not in values)
        already_bound = dict(self.request.bound_path_params)
        already_bound.update(values)
        mapping = replace(
            self.request,
            path_params=remaining,
            bound_path_params=tuple(already_bound.items()),
        )
        return replace(self, input_schema=schema, request=mapping)


@dataclass(frozen=True, slots=True)
class StaticTool(ToolDefinition):
    text: str

    def as_dict(self) -> dict[str, Any]:
        return {**self.base_dict(kind="static"), "staticText": self.text}


@dataclass(frozen=True, slots=True)
class ClientTool(ToolDefinition):
    action: str

    def as_dict(self) -> dict[str, Any]:
        return {**self.base_dict(kind="client"), "action": self.action}


BrowserTool = RequestTool | StaticTool | ClientTool


@dataclass(frozen=True, slots=True)
class WebMCPManifest:
    tools: Collection[BrowserTool]
    context: Mapping[str, Any] = field(default_factory=dict)


def static_tool(
    *,
    name: str,
    description: str,
    text: str,
    input_schema: Mapping[str, Any] | None = None,
    read_only: bool = True,
    untrusted_content: bool = False,
) -> StaticTool:
    return StaticTool(
        name=name,
        description=description,
        input_schema=(
            deepcopy(dict(input_schema)) if input_schema is not None else empty_input_schema()
        ),
        read_only=read_only,
        untrusted_content=untrusted_content,
        text=text,
    )


def client_tool(
    *,
    name: str,
    action: str,
    description: str,
    input_schema: Mapping[str, Any] | None = None,
    read_only: bool = False,
    untrusted_content: bool = True,
) -> ClientTool:
    return ClientTool(
        name=name,
        description=description,
        input_schema=(
            deepcopy(dict(input_schema)) if input_schema is not None else empty_input_schema()
        ),
        read_only=read_only,
        untrusted_content=untrusted_content,
        action=action,
    )
