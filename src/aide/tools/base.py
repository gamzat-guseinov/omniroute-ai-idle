"""Core types: limits, the per-workspace tool context, tool definitions and results."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, Literal

from .errors import ToolError
from .gitignore import GitIgnore, scan_dir
from .sandbox import Sandbox
from .util import write_bytes_atomic


@dataclass
class Limits:
    max_read_bytes: int = 20_000_000       # refuse to scan bigger files
    max_read_lines: int = 2000             # lines returned per read_file call
    max_line_chars: int = 2000             # longer lines are cut in read_file
    max_write_bytes: int = 5_000_000
    max_output_chars: int = 30_000         # hard cap for any tool result
    max_search_file_bytes: int = 2_000_000
    search_time_budget: float = 20.0       # seconds


@dataclass
class ToolResult:
    ok: bool
    output: str
    changed: list[str] = field(default_factory=list)  # workspace-relative paths modified


@dataclass(frozen=True)
class Entry:
    rel: str          # workspace-relative POSIX path
    path: Path        # absolute path
    is_dir: bool      # real directory (a symlink to a directory is NOT a dir here)
    is_symlink: bool
    depth: int        # 1 = direct child of the start directory


class ToolContext:
    """Everything a tool needs: the sandbox, gitignore rules, limits and the write hook."""

    def __init__(self, workspace: str | os.PathLike[str], *, limits: Limits | None = None,
                 before_write: Callable[[Path], None] | None = None) -> None:
        self.sandbox = Sandbox(workspace)
        self.ignore = GitIgnore(self.sandbox.root)
        self.limits = limits or Limits()
        # Called with the target path just before any file is created/changed/removed
        # (Stage 7 plugs the undo snapshots in here).
        self.before_write = before_write

    def rel(self, path: Path) -> str:
        return self.sandbox.relative(path)

    def write_bytes(self, path: Path, data: bytes) -> None:
        self.sandbox.check_writable(path)
        if self.before_write:
            self.before_write(path)
        try:
            write_bytes_atomic(path, data)
        except OSError as exc:
            raise ToolError(f"Cannot write '{self.rel(path)}': {exc.strerror or exc}") from exc

    def iter_entries(self, start: Path, *, max_depth: int | None = None,
                     include_ignored: bool = False) -> Iterator[Entry]:
        """Walk `start` depth-first, pruning ignored directories. Symlinks are listed but
        never followed, so a walk cannot leave the workspace or loop."""
        self.ignore.begin()
        root = self.sandbox.root

        def scan(directory: Path, depth: int) -> Iterator[Entry]:
            for e in scan_dir(directory):
                path = Path(e.path)
                is_dir = e.is_dir(follow_symlinks=False)
                rel = path.relative_to(root).as_posix()
                if self.ignore.skip(rel, is_dir, include_ignored=include_ignored):
                    continue
                yield Entry(rel, path, is_dir, e.is_symlink(), depth)
                if is_dir and (max_depth is None or depth < max_depth):
                    yield from scan(path, depth + 1)

        yield from scan(start, 1)


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]                       # JSON schema of the arguments
    handler: Callable[[ToolContext, dict[str, Any]], ToolResult]
    kind: Literal["read", "write", "exec"] = "read"  # drives permission prompts (Stage 4)
    # Optional: return a human-readable preview (a diff) without changing anything.
    preview: Callable[[ToolContext, dict[str, Any]], str] | None = None

    def schema(self) -> dict[str, Any]:
        """OpenAI-style function definition."""
        return {"type": "function", "function": {
            "name": self.name, "description": self.description, "parameters": self.parameters}}
