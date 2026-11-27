"""Small helpers shared by the tools."""
from __future__ import annotations

import difflib
import os
import stat
import tempfile
from pathlib import Path


def human_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"


def looks_binary(head: bytes) -> bool:
    return b"\x00" in head


def count_lines(text: str) -> int:
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def truncate_middle(text: str, limit: int) -> str:
    """Keep the start and end of an over-long text, dropping the middle."""
    if len(text) <= limit:
        return text
    head, tail = int(limit * 0.7), int(limit * 0.3)
    omitted = len(text) - head - tail
    return f"{text[:head]}\n[... {omitted} characters omitted ...]\n{text[-tail:]}"


def _split(text: str) -> list[str]:
    parts = text.split("\n")
    if parts and parts[-1] == "":  # the final newline is not an extra empty line
        parts.pop()
    return parts


def unified_diff(old: str, new: str, name: str, max_lines: int | None = None) -> str:
    diff = list(difflib.unified_diff(
        _split(old), _split(new),
        fromfile=f"a/{name}", tofile=f"b/{name}", lineterm="", n=3))
    if not diff:
        return "(no changes)"
    if max_lines is not None and len(diff) > max_lines:
        extra = len(diff) - max_lines
        diff = diff[:max_lines] + [f"... ({extra} more diff lines)"]
    return "\n".join(diff)


def write_bytes_atomic(path: Path, data: bytes) -> None:
    """Write `path` so readers never see a half-written file; keep the old file's mode."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        mode = stat.S_IMODE(path.stat().st_mode)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".aide-", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.chmod(tmp, mode)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    else:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)  # honours umask
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
