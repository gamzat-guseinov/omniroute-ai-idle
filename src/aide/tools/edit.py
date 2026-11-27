"""Editing tools: edit_file (exact search-and-replace) and apply_patch (unified diff).

Both fail loudly: the target text must be found, must be unique (unless told otherwise), and
nothing is written unless the whole edit can be applied.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .base import Tool, ToolContext, ToolResult
from .errors import ToolError
from .util import looks_binary, unified_diff

FUZZY_MAX_LINES = 20_000
DIFF_LINES_IN_RESULT = 60


# --------------------------------------------------------------------------- loading
def _load(ctx: ToolContext, given: str) -> tuple[Path, str, str]:
    path = ctx.sandbox.resolve(given, must_exist=True)
    ctx.sandbox.check_writable(path)
    rel = ctx.rel(path)
    if path.is_dir():
        raise ToolError(f"'{rel}' is a directory.")
    if path.stat().st_size > ctx.limits.max_read_bytes:
        raise ToolError(f"'{rel}' is too large to edit safely.")
    raw = path.read_bytes()
    if looks_binary(raw[:8192]):
        raise ToolError(f"'{rel}' looks like a binary file; refusing to edit it.")
    try:
        return path, rel, raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ToolError(f"'{rel}' is not valid UTF-8; refusing to edit it to avoid corrupting it.") from None


def _uses_crlf(text: str) -> bool:
    crlf = text.count("\r\n")
    return crlf > 0 and crlf > text.count("\n") - crlf


@dataclass
class Plan:
    path: Path
    rel: str
    old_text: str
    new_text: str
    summary: str


# --------------------------------------------------------------------------- hints
def _numbered(lines: list[str], first: int) -> str:
    return "\n".join(f"{first + i:>6}\t{ln}" for i, ln in enumerate(lines))


def closest_hint(text: str, old: str) -> str:
    """Explain why `old` was not found: prefix contamination, whitespace, or a near match."""
    old_lines = old.strip("\n").split("\n")
    if not any(ln.strip() for ln in old_lines):
        return ""
    hints = []
    if all(re.match(r"^\s*\d+\t", ln) for ln in old_lines if ln.strip()):
        hints.append("old_text seems to contain the line-number prefixes shown by read_file "
                     "(number + tab). Remove them: only the file's own text belongs in old_text.")
    lines = text.split("\n")
    stripped = [ln.strip() for ln in lines]
    target = [ln.strip() for ln in old_lines]
    k = len(target)
    for i in range(len(stripped) - k + 1):
        if stripped[i:i + k] == target:
            hints.append(f"A block with the same text but different indentation/whitespace exists "
                         f"at lines {i + 1}-{i + k}. Copy it exactly from read_file output:\n"
                         + _numbered(lines[i:i + min(k, 8)], i + 1))
            return "\n".join(hints)
    if len(lines) <= FUZZY_MAX_LINES and k <= 200:
        first = target[0]
        best_ratio, best_at = 0.0, -1
        candidates = [idx for idx, s in enumerate(stripped)
                      if s and difflib.SequenceMatcher(None, first, s).quick_ratio() >= 0.6]
        for idx in candidates[:200]:
            window = stripped[idx:idx + k]
            ratio = difflib.SequenceMatcher(None, "\n".join(target), "\n".join(window)).ratio()
            if ratio > best_ratio:
                best_ratio, best_at = ratio, idx
        if best_at >= 0 and best_ratio >= 0.6:
            hints.append(f"Closest match ({best_ratio:.0%} similar) starts at line {best_at + 1}:\n"
                         + _numbered(lines[best_at:best_at + min(k, 8)], best_at + 1))
    return "\n".join(hints)


# --------------------------------------------------------------------------- edit_file
def _plan_edit(ctx: ToolContext, a: dict[str, Any]) -> Plan:
    path, rel, text = _load(ctx, a["path"])
    old, new = a["old_text"], a["new_text"]
    replace_all = a.get("replace_all", False)
    if old == "":
        raise ToolError("old_text must not be empty. To create a file use write_file.")
    if old == new:
        raise ToolError("old_text and new_text are identical; nothing to change.")

    # The model writes \n; adapt to the file's line endings if needed.
    variants = [(old, new)]
    if _uses_crlf(text) and "\r\n" not in old:
        variants.append((old.replace("\n", "\r\n"), new.replace("\n", "\r\n")))
    elif not _uses_crlf(text) and "\r\n" in old:
        variants.append((old.replace("\r\n", "\n"), new.replace("\r\n", "\n")))
    chosen = next(((o, n) for o, n in variants if o in text), None)
    if chosen is None:
        hint = closest_hint(text, old)
        raise ToolError(f"old_text was not found in {rel}. It must match the file exactly, "
                        f"including indentation and line breaks. No changes were made."
                        + (f"\n{hint}" if hint else ""))
    old, new = chosen

    count = text.count(old)
    if count > 1 and not replace_all:
        lines_at, pos = [], text.find(old)
        while pos != -1 and len(lines_at) < 10:
            lines_at.append(text.count("\n", 0, pos) + 1)
            pos = text.find(old, pos + len(old))
        raise ToolError(f"old_text appears {count} times in {rel} (starting at lines "
                        f"{', '.join(map(str, lines_at))}). Include more surrounding lines to make "
                        f"it unique, or set replace_all=true to change every occurrence. "
                        f"No changes were made.")
    new_text = text.replace(old, new) if replace_all else text.replace(old, new, 1)
    n = count if replace_all else 1
    return Plan(path, rel, text, new_text, f"Edited {rel}: replaced {n} occurrence{'s' if n != 1 else ''}.")


# --------------------------------------------------------------------------- apply_patch
_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


@dataclass
class Hunk:
    old_start: int
    lines: list[tuple[str, str]]  # (" " | "-" | "+", text)


def parse_patch(patch: str) -> list[Hunk]:
    lines = patch.replace("\r\n", "\n").split("\n")
    while lines and lines[0].strip().startswith("```"):
        lines.pop(0)
    while lines and (lines[-1] == "" or lines[-1].strip().startswith("```")):
        lines.pop()
    hunks: list[Hunk] = []
    current: Hunk | None = None
    for raw in lines:
        m = _HUNK_RE.match(raw)
        if m:
            current = Hunk(int(m.group(1)), [])
            hunks.append(current)
            continue
        if current is None:
            continue  # file headers ("diff --git", "---", "+++", "index ...") before the first hunk
        if raw.startswith("diff --git "):
            raise ToolError("apply_patch handles one file per call; this patch contains several.")
        if raw.startswith("\\"):
            continue  # "\ No newline at end of file"
        if raw == "":
            current.lines.append((" ", ""))  # models often drop the space of a blank context line
        elif raw[0] in " +-":
            current.lines.append((raw[0], raw[1:]))
        else:
            raise ToolError(f"Malformed patch line {raw!r}: every line inside a hunk must start "
                            f"with ' ' (context), '-' (remove) or '+' (add).")
    if not hunks:
        raise ToolError("No hunks found. A patch needs '@@ -a,b +c,d @@' headers followed by "
                        "lines starting with ' ', '-' or '+'.")
    for i, h in enumerate(hunks, 1):
        if not any(op in "+-" for op, _ in h.lines):
            raise ToolError(f"Hunk {i} changes nothing (it has no '+' or '-' lines).")
    return hunks


def _find_all(norm_lines: list[str], block: list[str], fold) -> list[int]:
    k = len(block)
    target = [fold(x) for x in block]
    hay = [fold(x) for x in norm_lines]
    return [i for i in range(len(hay) - k + 1) if hay[i] == target[0] and hay[i:i + k] == target]


def _plan_patch(ctx: ToolContext, a: dict[str, Any]) -> Plan:
    path, rel, text = _load(ctx, a["path"])
    hunks = parse_patch(a["patch"])
    crlf = _uses_crlf(text)
    had_final_newline = text.endswith("\n")
    raw_lines = text.split("\n") if text else []
    if had_final_newline:
        raw_lines.pop()
    norm = [ln.rstrip("\r") for ln in raw_lines]

    located: list[tuple[int, int, Hunk]] = []   # (start index, old length, hunk)
    for n, h in enumerate(hunks, 1):
        old_block = [t for op, t in h.lines if op in " -"]
        if not old_block:  # pure insertion without context: only safe at a known position
            if raw_lines:
                idx = min(max(h.old_start, 0), len(raw_lines))
            else:
                idx = 0
            located.append((idx, 0, h))
            continue
        hits = _find_all(norm, old_block, lambda s: s)
        if not hits:  # tolerate trailing-whitespace differences only
            hits = _find_all(norm, old_block, str.rstrip)
        if not hits:
            hint = closest_hint(text, "\n".join(old_block))
            raise ToolError(f"Hunk {n} does not match {rel}: its context/removed lines were not "
                            f"found. Re-read the file and regenerate the patch. No changes were "
                            f"made." + (f"\n{hint}" if hint else ""))
        if len(hits) > 1:
            exact = [i for i in hits if i == h.old_start - 1]
            if len(exact) != 1:
                where = ", ".join(str(i + 1) for i in hits[:8])
                raise ToolError(f"Hunk {n} matches {len(hits)} places in {rel} (lines {where}). "
                                f"Add more context lines so it matches exactly once. No changes "
                                f"were made.")
            hits = exact
        located.append((hits[0], len(old_block), h))

    located.sort(key=lambda t: t[0])
    for (s1, l1, _), (s2, _l2, _h) in zip(located, located[1:]):
        if s1 + l1 > s2:
            raise ToolError("Two hunks overlap. Merge them into one hunk. No changes were made.")

    out = list(raw_lines)
    for start, length, h in reversed(located):
        replacement: list[str] = []
        p = start
        for op, t in h.lines:
            if op == " ":
                replacement.append(raw_lines[p])   # keep the file's own text for context lines
                p += 1
            elif op == "-":
                p += 1
            else:
                replacement.append(t + ("\r" if crlf else ""))
        out[start:start + length] = replacement

    new_text = "\n".join(out)
    if out and (had_final_newline or not text):
        new_text += "\n"
    n_hunks = len(hunks)
    return Plan(path, rel, text, new_text,
                f"Patched {rel}: applied {n_hunks} hunk{'s' if n_hunks != 1 else ''}.")


# --------------------------------------------------------------------------- tool glue
def _run(planner):
    def handler(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
        plan: Plan = planner(ctx, a)
        if plan.new_text == plan.old_text:
            raise ToolError("The edit would not change the file.")
        ctx.write_bytes(plan.path, plan.new_text.encode("utf-8"))
        diff = unified_diff(plan.old_text, plan.new_text, plan.rel, max_lines=DIFF_LINES_IN_RESULT)
        return ToolResult(True, f"{plan.summary}\n{diff}", changed=[plan.rel])
    return handler


def _preview(planner):
    def preview(ctx: ToolContext, a: dict[str, Any]) -> str:
        plan: Plan = planner(ctx, a)
        return unified_diff(plan.old_text, plan.new_text, plan.rel)
    return preview


TOOLS = [
    Tool(
        name="edit_file",
        description=("Replace exact text in an existing file. old_text must match the file "
                     "character for character (indentation included) and must occur exactly once "
                     "unless replace_all=true; include a few surrounding lines to make it unique. "
                     "Read the file first and copy old_text from it WITHOUT the line-number "
                     "column. Make several small edits rather than rewriting the file."),
        parameters={"type": "object", "properties": {
            "path": {"type": "string", "description": "File to edit (must exist)."},
            "old_text": {"type": "string", "description": "Exact text to replace."},
            "new_text": {"type": "string", "description": "Replacement text (may be empty to delete)."},
            "replace_all": {"type": "boolean", "description": "Replace every occurrence. Default false."},
        }, "required": ["path", "old_text", "new_text"]},
        handler=_run(_plan_edit), kind="write", preview=_preview(_plan_edit),
    ),
    Tool(
        name="apply_patch",
        description=("Apply a unified diff (one or more '@@ -a,b +c,d @@' hunks with ' ', '-', '+' "
                     "lines) to ONE existing file. Line numbers may be approximate: hunks are "
                     "located by their content, which must match exactly once. Either all hunks "
                     "apply or none do. Good for several changes in the same file."),
        parameters={"type": "object", "properties": {
            "path": {"type": "string", "description": "File to patch (must exist)."},
            "patch": {"type": "string", "description": "The unified diff (file headers optional)."},
        }, "required": ["path", "patch"]},
        handler=_run(_plan_patch), kind="write", preview=_preview(_plan_patch),
    ),
]
