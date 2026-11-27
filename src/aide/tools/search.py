"""search: grep-style text search across the workspace (pure Python, respects .gitignore)."""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from .base import Tool, ToolContext, ToolResult
from .errors import ToolError
from .gitignore import compile_glob
from .util import looks_binary

MAX_MATCH_CHARS = 2000    # only this much of a line is matched (bounds regex cost)
MAX_SHOWN_CHARS = 300     # only this much of a matching line is displayed


def _clip(text: str) -> str:
    return text if len(text) <= MAX_SHOWN_CHARS else text[:MAX_SHOWN_CHARS] + " ..."


def _search(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    pattern: str = a["pattern"]
    if not pattern:
        raise ToolError("pattern must not be empty.")
    flags = re.IGNORECASE if a.get("ignore_case", False) else 0
    try:
        rx = re.compile(re.escape(pattern) if a.get("literal", False) else pattern, flags)
    except re.error as exc:
        raise ToolError(f"Invalid regular expression: {exc}. "
                        f"Set literal=true to search for the text as-is.") from exc

    start = ctx.sandbox.resolve(a.get("path", "."), must_exist=True)
    glob = compile_glob(a["glob"]) if a.get("glob") else None
    context = a.get("context", 0)
    max_results = a.get("max_results", 100)
    files_only = a.get("files_only", False)
    include_ignored = a.get("include_ignored", False)
    lim = ctx.limits

    if start.is_file():
        files = [start]
        base = start.parent
    else:
        base = start
        files = (e.path for e in ctx.iter_entries(start, include_ignored=include_ignored)
                 if not e.is_dir and not e.is_symlink)

    deadline = time.monotonic() + lim.search_time_budget
    out: list[str] = []
    matches = files_with_hits = searched = skipped = 0
    stopped = ""

    for path in files:
        rel_to_base = path.relative_to(base).as_posix()
        if glob and not glob(rel_to_base):
            continue
        if time.monotonic() > deadline:
            stopped = f"stopped after {lim.search_time_budget:g}s; results are incomplete"
            break
        try:
            if path.stat().st_size > lim.max_search_file_bytes:
                skipped += 1
                continue
            data = path.read_bytes()
        except OSError:
            skipped += 1
            continue
        if looks_binary(data[:8192]):
            skipped += 1
            continue
        searched += 1
        lines = data.decode("utf-8", errors="replace").split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        lines = [ln.rstrip("\r") for ln in lines]
        hit_idx = [i for i, ln in enumerate(lines) if rx.search(ln[:MAX_MATCH_CHARS])]
        if not hit_idx:
            continue
        files_with_hits += 1
        rel = ctx.rel(path)
        room = max_results - matches
        if len(hit_idx) > room:
            hit_idx = hit_idx[:room]
            stopped = f"stopped after {max_results} matches; narrow the search or raise max_results"
        matches += len(hit_idx)
        if files_only:
            out.append(f"{rel}  ({len(hit_idx)} match{'es' if len(hit_idx) != 1 else ''})")
        else:
            hits = set(hit_idx)
            wanted = sorted({j for i in hit_idx
                             for j in range(max(0, i - context), min(len(lines), i + context + 1))})
            prev = None
            for j in wanted:
                if context and prev is not None and j != prev + 1:
                    out.append("--")
                sep = ":" if j in hits else "-"
                out.append(f"{rel}{sep}{j + 1}{sep}{_clip(lines[j])}")
                prev = j
        if stopped:
            break

    if not matches:
        hint = "" if include_ignored else " (files ignored by .gitignore are skipped; set include_ignored=true to search them)"
        note = f"; {stopped}" if stopped else ""
        return ToolResult(True, f"No matches for {pattern!r} in {searched} files searched{hint}{note}.")
    summary = f"{matches} match{'es' if matches != 1 else ''} in {files_with_hits} file(s)"
    if skipped:
        summary += f" ({skipped} binary/large/unreadable files skipped)"
    if stopped:
        summary += f" - {stopped}"
    body = "\n".join(out)
    fmt = "path:line:text (context lines use '-')" if not files_only else "path (match count)"
    return ToolResult(True, f"{summary}. Format: {fmt}\n{body}")


TOOLS = [
    Tool(
        name="search",
        description=("Search file contents in the workspace (like grep -rn). `pattern` is a Python "
                     "regular expression unless literal=true. Returns path:line:text. Respects "
                     ".gitignore and skips binary/huge files. Use `glob` (e.g. '*.py' or "
                     "'src/**/*.ts') to restrict which files are searched."),
        parameters={"type": "object", "properties": {
            "pattern": {"type": "string", "description": "Regex (or plain text with literal=true)."},
            "path": {"type": "string", "description": "Directory or file to search. Default: the workspace root."},
            "glob": {"type": "string", "description": "File filter: '*.py', '*.{ts,tsx}', 'src/**/*.go'."},
            "ignore_case": {"type": "boolean", "description": "Case-insensitive. Default false."},
            "literal": {"type": "boolean", "description": "Treat pattern as plain text. Default false."},
            "context": {"type": "integer", "minimum": 0, "maximum": 5,
                        "description": "Lines of context around each match. Default 0."},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 500,
                            "description": "Maximum matches returned. Default 100."},
            "files_only": {"type": "boolean",
                           "description": "Only list files containing matches, with counts."},
            "include_ignored": {"type": "boolean", "description": "Also search ignored files."},
        }, "required": ["pattern"]},
        handler=_search,
    ),
]
