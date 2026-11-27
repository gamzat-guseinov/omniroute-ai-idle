"""Optional debug log of API traffic. API keys are redacted before anything is written."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .config import state_dir

_SECRET_KEYS = {"authorization", "api_key", "apikey", "x-api-key", "proxy-authorization"}
_TOKEN_RE = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+|sk-[A-Za-z0-9_-]{8,}")


def default_log_path() -> Path:
    return state_dir() / "debug.log"


class DebugLog:
    """Append-only JSON-lines log. `DebugLog(None)` is a cheap no-op logger."""

    def __init__(self, path: Path | None, secrets: Iterable[str] = ()) -> None:
        self.path = path
        # Longest first so a key that contains another one is fully replaced.
        self._secrets = sorted({s for s in secrets if s and len(s) >= 6}, key=len, reverse=True)
        self._fh = None
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            os.fchmod(fd, 0o600)
            self._fh = os.fdopen(fd, "a", encoding="utf-8")

    @property
    def enabled(self) -> bool:
        return self._fh is not None

    def redact(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, "***")
        return _TOKEN_RE.sub(lambda m: (m.group(1) or "") + "***", text)

    def _scrub(self, obj: Any) -> Any:
        if isinstance(obj, dict):
            return {
                k: "***" if str(k).lower() in _SECRET_KEYS else self._scrub(v)
                for k, v in obj.items()
            }
        if isinstance(obj, (list, tuple)):
            return [self._scrub(v) for v in obj]
        return obj

    def log(self, kind: str, data: Any = None) -> None:
        if self._fh is None:
            return
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "kind": kind,
            "data": self._scrub(data),
        }
        line = self.redact(json.dumps(entry, ensure_ascii=False, default=str))
        self._fh.write(line + "\n")
        self._fh.flush()

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None
