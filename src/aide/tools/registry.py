"""Tool registry: schemas for the model, argument validation, and safe dispatch."""
from __future__ import annotations

import os
from typing import Any, Callable, Iterable

from .base import Limits, Tool, ToolContext, ToolResult
from .errors import ToolError
from .util import truncate_middle


def _coerce(name: str, value: Any, spec: dict[str, Any]) -> Any:
    kind = spec.get("type")
    if kind == "string":
        if not isinstance(value, str):
            raise ToolError(f"Argument '{name}' must be a string.")
        result: Any = value
    elif kind == "integer":
        if isinstance(value, bool):
            raise ToolError(f"Argument '{name}' must be an integer.")
        try:
            if isinstance(value, float) and not value.is_integer():
                raise ValueError
            result = int(value.strip() if isinstance(value, str) else value)
        except (TypeError, ValueError):
            raise ToolError(f"Argument '{name}' must be an integer, got {value!r}.") from None
        if "minimum" in spec and result < spec["minimum"]:
            raise ToolError(f"Argument '{name}' must be at least {spec['minimum']}.")
        if "maximum" in spec and result > spec["maximum"]:
            result = spec["maximum"]  # clamp instead of failing
    elif kind == "boolean":
        if isinstance(value, bool):
            result = value
        elif isinstance(value, str) and value.strip().lower() in ("true", "false"):
            result = value.strip().lower() == "true"
        else:
            raise ToolError(f"Argument '{name}' must be true or false.")
    elif kind == "array":
        if not isinstance(value, list):
            raise ToolError(f"Argument '{name}' must be an array.")
        result = value
    else:
        result = value
    if "enum" in spec and result not in spec["enum"]:
        raise ToolError(f"Argument '{name}' must be one of: {', '.join(map(str, spec['enum']))}.")
    return result


def validate_args(schema: dict[str, Any], args: Any) -> dict[str, Any]:
    """Check model-supplied arguments against a tool schema; tolerate harmless type slips
    (numbers sent as strings, "true" for true). null counts as "not provided"."""
    if not isinstance(args, dict):
        raise ToolError("Tool arguments must be a JSON object.")
    props: dict[str, Any] = schema.get("properties", {})
    unknown = sorted(k for k in args if k not in props)
    if unknown:
        raise ToolError(f"Unknown argument(s): {', '.join(unknown)}. "
                        f"Valid arguments: {', '.join(props)}.")
    missing = [k for k in schema.get("required", []) if args.get(k) is None]
    if missing:
        raise ToolError(f"Missing required argument(s): {', '.join(missing)}.")
    return {k: _coerce(k, v, props[k]) for k, v in args.items() if v is not None}


class ToolRegistry:
    def __init__(self, ctx: ToolContext, tools: Iterable[Tool] = ()) -> None:
        self.ctx = ctx
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def names(self) -> list[str]:
        return list(self._tools)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def schemas(self) -> list[dict[str, Any]]:
        return [t.schema() for t in self._tools.values()]

    def preview(self, name: str, args: dict[str, Any]) -> str | None:
        """Diff of what a write tool would do, or None if the tool has no preview.
        Raises ToolError if the call is invalid (same errors execute() would report)."""
        tool = self._tools.get(name)
        if tool is None or tool.preview is None:
            return None
        return tool.preview(self.ctx, validate_args(tool.parameters, args))

    def execute(self, name: str, args: Any) -> ToolResult:
        """Run a tool. Never raises: every failure becomes ToolResult(ok=False, ...)."""
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(False, f"Unknown tool '{name}'. Available tools: "
                                     f"{', '.join(self._tools)}.")
        try:
            result = tool.handler(self.ctx, validate_args(tool.parameters, args))
        except ToolError as exc:
            return ToolResult(False, str(exc))
        except Exception as exc:  # a bug in a tool must not kill the agent loop
            return ToolResult(False, f"Internal error in {name}: {type(exc).__name__}: {exc}")
        result.output = truncate_middle(result.output, self.ctx.limits.max_output_chars)
        return result


def create_registry(workspace: str | os.PathLike[str], *, limits: Limits | None = None,
                    before_write: Callable[[Any], None] | None = None) -> ToolRegistry:
    """Build a registry with all file tools for `workspace`."""
    from . import edit, fs, search  # late import: they import base
    ctx = ToolContext(workspace, limits=limits, before_write=before_write)
    return ToolRegistry(ctx, [*fs.TOOLS, *search.TOOLS, *edit.TOOLS])
