"""Error types for tools."""
from __future__ import annotations


class ToolError(Exception):
    """A failure the model can act on. The message is sent back to the model verbatim,
    so it should say what went wrong and, where possible, how to fix the call."""


class SandboxError(ToolError):
    """A path was rejected because it would leave the workspace (or is protected)."""
