from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from copy import deepcopy
from typing import Any

from fastapi import FastAPI
from fastapi.routing import APIRoute

from .decorators import metadata_for
from .exceptions import RouteConversionError
from .models import RequestMapping, RequestTool, ToolMetadata

HTTP_METHODS = ("get", "post", "put", "patch", "delete")
TOOL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


class ToolDiscovery:
    def __init__(
        self,
        app: FastAPI,
        *,
        include_operations: Collection[str] = (),
        include_tags: Collection[str] = (),
        exclude_operations: Collection[str] = (),
        expose_all: bool = False,
    ) -> None:
        self.app = app
        self.include_operations = frozenset(include_operations)
        self.include_tags = frozenset(include_tags)
        self.exclude_operations = frozenset(exclude_operations)
        self.expose_all = expose_all

    def discover(self) -> list[RequestTool]:
        specification = self.app.openapi()
        route_index = self._route_index()
        tools: list[RequestTool] = []
        names: set[str] = set()

        for path, path_item in specification.get("paths", {}).items():
            if not isinstance(path_item, Mapping):
                continue
            for method in HTTP_METHODS:
                operation = path_item.get(method)
                if not isinstance(operation, Mapping):
                    continue
                operation_id = operation.get("operationId")
                if not isinstance(operation_id, str):
                    continue
                route = route_index.get((path, method.upper()))
                metadata = metadata_for(route.endpoint) if route is not None else None
                if not self._selected(operation_id, operation, metadata):
                    continue

                tool = self._build_tool(
                    specification=specification,
                    path=path,
                    path_item=path_item,
                    method=method.upper(),
                    operation_id=operation_id,
                    operation=operation,
                    metadata=metadata,
                )
                if tool.name in names:
                    raise RouteConversionError(f"duplicate WebMCP tool name: {tool.name}")
                names.add(tool.name)
                tools.append(tool)

        return tools

    def _route_index(self) -> dict[tuple[str, str], APIRoute]:
        index: dict[tuple[str, str], APIRoute] = {}
        for route in self.app.routes:
            if not isinstance(route, APIRoute) or not route.include_in_schema:
                continue
            for method in route.methods or ():
                index[(route.path_format, method.upper())] = route
        return index

    def _selected(
        self,
        operation_id: str,
        operation: Mapping[str, Any],
        metadata: ToolMetadata | None,
    ) -> bool:
        if operation_id in self.exclude_operations:
            return False
        tags = {tag for tag in operation.get("tags", []) if isinstance(tag, str)}
        return (
            metadata is not None
            or self.expose_all
            or operation_id in self.include_operations
            or bool(tags & self.include_tags)
        )

    def _build_tool(
        self,
        *,
        specification: Mapping[str, Any],
        path: str,
        path_item: Mapping[str, Any],
        method: str,
        operation_id: str,
        operation: Mapping[str, Any],
        metadata: ToolMetadata | None,
    ) -> RequestTool:
        name = metadata.name if metadata and metadata.name else operation_id
        if not TOOL_NAME_PATTERN.fullmatch(name):
            raise RouteConversionError(f"tool name {name!r} must match {TOOL_NAME_PATTERN.pattern}")

        description = self._description(operation_id, operation, metadata)
        input_schema, mapping = self._input_contract(
            specification=specification,
            path=path,
            path_item=path_item,
            method=method,
            operation=operation,
            metadata=metadata,
        )
        read_only = (
            metadata.read_only
            if metadata is not None and metadata.read_only is not None
            else method in {"GET", "HEAD"}
        )
        untrusted_content = metadata.untrusted_content if metadata is not None else True
        return RequestTool(
            name=name,
            description=description,
            input_schema=input_schema,
            read_only=read_only,
            untrusted_content=untrusted_content,
            request=mapping,
        )

    def _description(
        self,
        operation_id: str,
        operation: Mapping[str, Any],
        metadata: ToolMetadata | None,
    ) -> str:
        if metadata is not None and metadata.description:
            return metadata.description
        summary = operation.get("summary")
        if isinstance(summary, str) and summary.strip():
            return summary.strip()
        description = operation.get("description")
        if isinstance(description, str) and description.strip():
            return description.strip().split("\n\n", 1)[0]
        return operation_id.replace("_", " ").strip().capitalize()

    def _input_contract(
        self,
        *,
        specification: Mapping[str, Any],
        path: str,
        path_item: Mapping[str, Any],
        method: str,
        operation: Mapping[str, Any],
        metadata: ToolMetadata | None,
    ) -> tuple[dict[str, Any], RequestMapping]:
        properties: dict[str, Any] = {}
        required: list[str] = []
        path_params: list[str] = []
        query_params: list[str] = []
        body_params: list[str] = []
        body_value_param: str | None = None
        header_params: list[tuple[str, str]] = []
        configured_headers = dict(metadata.header_params) if metadata is not None else {}
        matched_headers: set[str] = set()

        parameters = [*path_item.get("parameters", []), *operation.get("parameters", [])]
        for raw_parameter in parameters:
            parameter = self._resolve_object(raw_parameter, specification)
            name = parameter.get("name")
            location = parameter.get("in")
            if not isinstance(name, str) or not isinstance(location, str):
                continue
            if location == "header":
                tool_name = next(
                    (
                        input_name
                        for input_name, header_name in configured_headers.items()
                        if header_name.casefold() == name.casefold()
                    ),
                    None,
                )
                if tool_name is not None:
                    schema = self._resolve_schema(parameter.get("schema", {}), specification)
                    if isinstance(parameter.get("description"), str):
                        schema.setdefault("description", parameter["description"])
                    self._add_property(
                        properties,
                        tool_name,
                        schema,
                        method=method,
                        path=path,
                    )
                    if parameter.get("required") and tool_name not in required:
                        required.append(tool_name)
                    header_params.append((tool_name, name))
                    matched_headers.add(tool_name)
                    continue
            if location not in {"path", "query"}:
                if parameter.get("required"):
                    raise RouteConversionError(
                        f"{method} {path} requires unsupported {location} parameter {name!r}"
                    )
                continue
            schema = self._resolve_schema(parameter.get("schema", {}), specification)
            if isinstance(parameter.get("description"), str):
                schema.setdefault("description", parameter["description"])
            self._add_property(properties, name, schema, method=method, path=path)
            if parameter.get("required") and name not in required:
                required.append(name)
            (path_params if location == "path" else query_params).append(name)

        unmatched_headers = set(configured_headers) - matched_headers
        if unmatched_headers:
            names = ", ".join(sorted(unmatched_headers))
            raise RouteConversionError(
                f"{method} {path} declares WebMCP header inputs not found in OpenAPI: {names}"
            )

        request_body = operation.get("requestBody")
        if request_body is not None:
            body = self._resolve_object(request_body, specification)
            media_type = self._json_media_type(body.get("content", {}))
            if media_type is None:
                raise RouteConversionError(f"{method} {path} has no application/json request body")
            body_schema = self._resolve_schema(media_type.get("schema", {}), specification)
            if body_schema.get("type") == "object" and isinstance(
                body_schema.get("properties"), Mapping
            ):
                for name, schema in body_schema["properties"].items():
                    if not isinstance(name, str) or not isinstance(schema, Mapping):
                        continue
                    self._add_property(
                        properties,
                        name,
                        deepcopy(dict(schema)),
                        method=method,
                        path=path,
                    )
                    body_params.append(name)
                for name in body_schema.get("required", []):
                    if isinstance(name, str) and name not in required:
                        required.append(name)
            else:
                body_value_param = "body"
                self._add_property(
                    properties,
                    body_value_param,
                    body_schema,
                    method=method,
                    path=path,
                )
                if body.get("required"):
                    required.append(body_value_param)

        return (
            {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
            RequestMapping(
                method=method,
                path=path,
                path_params=tuple(path_params),
                query_params=tuple(query_params),
                body_params=tuple(body_params),
                body_value_param=body_value_param,
                header_params=tuple(header_params),
            ),
        )

    def _add_property(
        self,
        properties: dict[str, Any],
        name: str,
        schema: dict[str, Any],
        *,
        method: str,
        path: str,
    ) -> None:
        if name in properties:
            raise RouteConversionError(
                f"{method} {path} maps more than one input to property {name!r}"
            )
        properties[name] = schema

    def _json_media_type(self, content: Any) -> Mapping[str, Any] | None:
        if not isinstance(content, Mapping):
            return None
        direct = content.get("application/json")
        if isinstance(direct, Mapping):
            return direct
        for media_type, value in content.items():
            if (
                isinstance(media_type, str)
                and media_type.endswith("+json")
                and isinstance(value, Mapping)
            ):
                return value
        return None

    def _resolve_object(self, value: Any, specification: Mapping[str, Any]) -> dict[str, Any]:
        resolved = self._resolve_schema(value, specification)
        if not isinstance(resolved, dict):
            raise RouteConversionError("OpenAPI object did not resolve to a mapping")
        return resolved

    def _resolve_schema(
        self,
        value: Any,
        specification: Mapping[str, Any],
        stack: tuple[str, ...] = (),
    ) -> Any:
        if isinstance(value, list):
            return [self._resolve_schema(item, specification, stack) for item in value]
        if not isinstance(value, Mapping):
            return deepcopy(value)

        reference = value.get("$ref")
        if isinstance(reference, str):
            if not reference.startswith("#/"):
                raise RouteConversionError(
                    f"external OpenAPI reference is unsupported: {reference}"
                )
            if reference in stack:
                raise RouteConversionError(f"cyclic OpenAPI schema is unsupported: {reference}")
            target: Any = specification
            for part in reference[2:].split("/"):
                key = part.replace("~1", "/").replace("~0", "~")
                if not isinstance(target, Mapping) or key not in target:
                    raise RouteConversionError(f"unresolved OpenAPI reference: {reference}")
                target = target[key]
            resolved = self._resolve_schema(target, specification, (*stack, reference))
            if not isinstance(resolved, dict):
                raise RouteConversionError(f"OpenAPI reference is not an object: {reference}")
            siblings = {key: item for key, item in value.items() if key != "$ref"}
            resolved.update(self._resolve_schema(siblings, specification, stack))
            return resolved

        return {
            key: self._resolve_schema(item, specification, stack)
            for key, item in value.items()
            if key not in {"$defs", "definitions"}
        }
