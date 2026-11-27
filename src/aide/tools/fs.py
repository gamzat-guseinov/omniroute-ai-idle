"""File-system tools: list_dir, tree, read_file, write_file."""
from __future__ import annotations

import os
from typing import Any

from .base import Entry, Tool, ToolContext, ToolResult
from .errors import ToolError
from .util import count_lines, human_size, looks_binary, unified_diff


def _label(e: Entry, *, size: bool) -> str:
    name = e.path.name
    if e.is_symlink:
        try:
            return f"{name}@ -> {os.readlink(e.path)}"
        except OSError:
            return f"{name}@"
    if e.is_dir:
        return name + "/"
    if size:
        try:
            return f"{name}  ({human_size(e.path.stat().st_size)})"
        except OSError:
            pass
    return name


def _existing_dir(ctx: ToolContext, given: str):
    path = ctx.sandbox.resolve(given, must_exist=True)
    if not path.is_dir():
        raise ToolError(f"'{given}' is a file, not a directory. Use read_file to see its content.")
    return path


# ------------------------------------------------------------------------ list_dir
def _list_dir(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    given = a.get("path", ".")
    path = _existing_dir(ctx, given)
    limit = a.get("max_entries", 200)
    entries = list(ctx.iter_entries(path, max_depth=1, include_ignored=a.get("include_ignored", False)))
    lines = [_label(e, size=True) for e in entries[:limit]]
    header = f"{ctx.rel(path)}/  ({len(entries)} entries)"
    if not entries:
        return ToolResult(True, header + "\n(empty, or everything in it is ignored)")
    if len(entries) > limit:
        lines.append(f"... {len(entries) - limit} more entries not shown (raise max_entries)")
    return ToolResult(True, header + "\n" + "\n".join(lines))


# ------------------------------------------------------------------------ tree
def _tree(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    given = a.get("path", ".")
    path = _existing_dir(ctx, given)
    depth, limit = a.get("max_depth", 3), a.get("max_entries", 300)
    shown, total = [], 0
    for e in ctx.iter_entries(path, max_depth=depth, include_ignored=a.get("include_ignored", False)):
        total += 1
        if len(shown) < limit:
            shown.append("  " * e.depth + _label(e, size=False))
    root = ctx.rel(path)
    out = [f"{root}/  (depth limit {depth})", *shown]
    if total > limit:
        out.append(f"... {total - limit} more entries not shown (raise max_entries, "
                   f"lower max_depth, or list a subdirectory)")
    if total == 0:
        out.append("(empty, or everything in it is ignored)")
    return ToolResult(True, "\n".join(out))


# ------------------------------------------------------------------------ read_file
def _read_file(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    given = a["path"]
    path = ctx.sandbox.resolve(given, must_exist=True)
    rel = ctx.rel(path)
    if path.is_dir():
        raise ToolError(f"'{rel}' is a directory. Use list_dir or tree.")
    size = path.stat().st_size
    lim = ctx.limits
    if size > lim.max_read_bytes:
        raise ToolError(f"'{rel}' is too large to read ({human_size(size)}). "
                        f"Use search to find the relevant part.")
    try:
        with open(path, "rb") as fh:
            head = fh.read(8192)
    except OSError as exc:
        raise ToolError(f"Cannot read '{rel}': {exc.strerror or exc}") from exc
    if looks_binary(head):
        raise ToolError(f"'{rel}' looks like a binary file ({human_size(size)}); not shown.")

    start = a.get("start_line", 1)
    requested_end = a.get("end_line")
    if requested_end is not None and requested_end < start:
        raise ToolError("end_line must be greater than or equal to start_line.")
    last = start + lim.max_read_lines - 1
    end = min(requested_end, last) if requested_end is not None else last

    shown: list[str] = []
    total = 0
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as fh:
        for total, line in enumerate(fh, 1):
            if start <= total <= end:
                text = line.rstrip("\r\n")
                if len(text) > lim.max_line_chars:
                    text = f"{text[:lim.max_line_chars]} ... [line cut, {len(text)} chars]"
                shown.append(f"{total:>6}\t{text}")
    if total == 0:
        return ToolResult(True, f"{rel}: (empty file)")
    if start > total:
        raise ToolError(f"'{rel}' has only {total} lines; start_line {start} is past the end.")
    last_shown = min(end, total)
    out = [f"{rel}  (lines {start}-{last_shown} of {total}; the number column is not part of the file)",
           *shown]
    if last_shown < total and (requested_end is None or requested_end > last_shown):
        out.append(f"[more lines follow: call read_file with start_line={last_shown + 1}]")
    return ToolResult(True, "\n".join(out))


# ------------------------------------------------------------------------ write_file
def _plan_write(ctx: ToolContext, a: dict[str, Any]):
    path = ctx.sandbox.resolve(a["path"])
    ctx.sandbox.check_writable(path)
    if path.is_dir():
        raise ToolError(f"'{ctx.rel(path)}' is a directory; give a file path.")
    content: str = a["content"]
    if len(content.encode("utf-8")) > ctx.limits.max_write_bytes:
        raise ToolError("Content is too large to write in one call.")
    return path, content


def _write_file(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    path, content = _plan_write(ctx, a)
    rel, existed = ctx.rel(path), path.exists()
    data = content.encode("utf-8")
    ctx.write_bytes(path, data)
    verb = "Overwrote" if existed else "Created"
    return ToolResult(True, f"{verb} {rel} ({count_lines(content)} lines, {human_size(len(data))})",
                      changed=[rel])


def _preview_write(ctx: ToolContext, a: dict[str, Any]) -> str:
    path, content = _plan_write(ctx, a)
    rel = ctx.rel(path)
    if not path.exists():
        return unified_diff("", content, rel, max_lines=200).replace(f"--- a/{rel}", "--- /dev/null")
    try:
        old = path.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        return f"(existing {rel} is binary or not UTF-8; it would be replaced)"
    return unified_diff(old, content, rel, max_lines=200)


PATH_DESC = "Path relative to the workspace root (absolute paths are accepted only inside it)."

TOOLS = [
    Tool(
        name="list_dir",
        description=("List the entries of ONE directory (directories first, with file sizes). "
                     "Respects .gitignore. Use tree for a recursive overview."),
        parameters={"type": "object", "properties": {
            "path": {"type": "string", "description": PATH_DESC + " Default: the workspace root."},
            "include_ignored": {"type": "boolean",
                                "description": "Also show files ignored by .gitignore. Default false."},
            "max_entries": {"type": "integer", "minimum": 1, "maximum": 1000,
                            "description": "Default 200."},
        }},
        handler=_list_dir,
    ),
    Tool(
        name="tree",
        description=("Show a directory tree (indented) to a limited depth. Respects .gitignore and "
                     "skips node_modules, __pycache__, virtualenvs. Good first step in an "
                     "unfamiliar project."),
        parameters={"type": "object", "properties": {
            "path": {"type": "string", "description": PATH_DESC + " Default: the workspace root."},
            "max_depth": {"type": "integer", "minimum": 1, "maximum": 8, "description": "Default 3."},
            "max_entries": {"type": "integer", "minimum": 1, "maximum": 1000,
                            "description": "Default 300."},
            "include_ignored": {"type": "boolean", "description": "Default false."},
        }},
        handler=_tree,
    ),
    Tool(
        name="read_file",
        description=("Read a text file. Output lines are prefixed with their line number (not part "
                     "of the file). At most 2000 lines per call; use start_line/end_line to page "
                     "through large files. Always read a file before editing it."),
        parameters={"type": "object", "properties": {
            "path": {"type": "string", "description": PATH_DESC},
            "start_line": {"type": "integer", "minimum": 1, "description": "First line, default 1."},
            "end_line": {"type": "integer", "minimum": 1, "description": "Last line (inclusive)."},
        }, "required": ["path"]},
        handler=_read_file,
    ),
    Tool(
        name="write_file",
        description=("Create a new file or completely overwrite an existing one with `content` "
                     "(parent directories are created). To change part of an existing file prefer "
                     "edit_file or apply_patch, which are safer and smaller."),
        parameters={"type": "object", "properties": {
            "path": {"type": "string", "description": PATH_DESC},
            "content": {"type": "string", "description": "The complete new file content."},
        }, "required": ["path", "content"]},
        handler=_write_file, kind="write", preview=_preview_write,
    ),
]
