"""Configuration handling for aide.

Precedence (highest first):
    command-line overrides > OMNIROUTE_* environment variables > config file > defaults

The config file may contain the API key, so it is always written with mode 600.
"""
from __future__ import annotations

import getpass
import json
import os
import stat
import sys
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Callable, Mapping

ENV_PREFIX = "OMNIROUTE_"
APP_DIR = "aide"


class ConfigError(Exception):
    """A problem the user can fix (bad config value, missing workspace, ...)."""


@dataclass
class Config:
    base_url: str = ""          # e.g. https://your-host/v1  (we append /chat/completions)
    api_key: str = ""
    model: str = ""
    temperature: float = 0.2
    max_tokens: int = 4096
    timeout: float = 120.0      # seconds of silence before a request counts as timed out
    max_retries: int = 4        # retries for rate limits, 5xx, timeouts, network errors
    stream_usage: bool = True   # ask the server to report token usage while streaming


_FIELD_NAMES = tuple(f.name for f in fields(Config))
_DEFAULTS = Config()
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


# --------------------------------------------------------------------------- paths
def config_path() -> Path:
    override = os.environ.get("AIDE_CONFIG")
    if override:
        return Path(override).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    return Path(base) / APP_DIR / "config.toml"


def state_dir() -> Path:
    """Where logs, sessions and undo snapshots live."""
    base = os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state")
    return Path(base) / APP_DIR


# --------------------------------------------------------------------------- loading
def _coerce(name: str, value: Any, source: str) -> Any:
    default = getattr(_DEFAULTS, name)
    try:
        if isinstance(default, bool):  # must come before int: bool is an int subclass
            if isinstance(value, bool):
                return value
            text = str(value).strip().lower()
            if text in _TRUE:
                return True
            if text in _FALSE:
                return False
            raise ValueError
        if isinstance(default, int):
            if isinstance(value, bool):
                raise ValueError
            if isinstance(value, float):
                if not value.is_integer():
                    raise ValueError
                return int(value)
            return int(str(value).strip())
        if isinstance(default, float):
            if isinstance(value, bool):
                raise ValueError
            return float(value)
        return str(value).strip()
    except (TypeError, ValueError):
        raise ConfigError(
            f"Invalid value {value!r} for '{name}' (from {source}); "
            f"expected {type(default).__name__}."
        ) from None


def _normalize_base_url(url: str) -> str:
    url = url.strip().rstrip("/")
    if url.endswith("/chat/completions"):
        url = url[: -len("/chat/completions")].rstrip("/")
    if url and not url.lower().startswith(("http://", "https://")):
        raise ConfigError(f"base_url must start with http:// or https:// (got {url!r}).")
    return url


def _validate(cfg: Config) -> None:
    if not 0.0 <= cfg.temperature <= 2.0:
        raise ConfigError(f"temperature must be between 0 and 2 (got {cfg.temperature}).")
    if cfg.max_tokens <= 0:
        raise ConfigError(f"max_tokens must be positive (got {cfg.max_tokens}).")
    if cfg.timeout <= 0:
        raise ConfigError(f"timeout must be positive (got {cfg.timeout}).")
    if cfg.max_retries < 0:
        raise ConfigError(f"max_retries cannot be negative (got {cfg.max_retries}).")


def _tighten_permissions(path: Path) -> None:
    try:
        if stat.S_IMODE(path.stat().st_mode) & 0o077:
            path.chmod(0o600)
            print(f"aide: {path} was readable by other users; permissions set to 600.",
                  file=sys.stderr)
    except OSError:
        pass


def read_file_values(path: Path) -> dict[str, Any]:
    """Raw (uncoerced) known settings stored in the config file; {} if it does not exist."""
    if not path.exists():
        return {}
    _tighten_permissions(path)
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError, OSError) as exc:
        raise ConfigError(f"Cannot read config file {path}: {exc}") from exc
    return {k: v for k, v in data.items() if k in _FIELD_NAMES}


def load_config(
    path: Path | None = None,
    *,
    env: Mapping[str, str] | None = None,
    overrides: Mapping[str, Any] | None = None,
) -> Config:
    env = os.environ if env is None else env
    path = path if path is not None else config_path()

    merged: dict[str, tuple[Any, str]] = {}
    for key, value in read_file_values(path).items():
        merged[key] = (value, str(path))
    for name in _FIELD_NAMES:
        var = ENV_PREFIX + name.upper()
        value = env.get(var)
        if value not in (None, ""):
            merged[name] = (value, f"environment variable {var}")
    for key, value in (overrides or {}).items():
        if key in _FIELD_NAMES and value is not None:
            merged[key] = (value, "the command line")

    cfg = Config(**{n: _coerce(n, v, src) for n, (v, src) in merged.items()})
    cfg.base_url = _normalize_base_url(cfg.base_url)
    _validate(cfg)
    return cfg


def missing_fields(cfg: Config) -> list[str]:
    return [n for n in ("base_url", "api_key", "model") if not getattr(cfg, n)]


# --------------------------------------------------------------------------- saving
def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    # A JSON string is a valid TOML basic string (except for a raw DEL character).
    return json.dumps(str(value), ensure_ascii=False).replace("\x7f", "\\u007f")


def write_config_values(values: Mapping[str, Any], path: Path) -> None:
    """Atomically write settings to `path` with permissions 600."""
    lines = [
        "# aide configuration. OMNIROUTE_* environment variables override these values.",
        "# This file may contain your API key - keep its permissions at 600.",
        "",
    ]
    lines += [f"{n} = {_toml_value(values[n])}" for n in _FIELD_NAMES if n in values]
    text = "\n".join(lines) + "\n"

    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            os.fchmod(fh.fileno(), 0o600)
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# --------------------------------------------------------------------------- wizard
def _mask(secret: str) -> str:
    return "****" + secret[-4:] if len(secret) > 8 else "****"


def run_wizard(
    path: Path,
    current: Config,
    *,
    force: bool = False,
    input_fn: Callable[[str], str] = input,
    secret_fn: Callable[[str], str] = getpass.getpass,
    out: Callable[[str], None] = print,
) -> None:
    """Interactively ask for missing essentials (all of them if `force`) and save them.

    Values that already come from the environment are never copied into the file.
    Pressing Enter on a value that is already set keeps it untouched.
    """
    stored = read_file_values(path)
    out("aide setup - connect to your OmniRoute endpoint.")
    out(f"Your answers are saved to {path} (permissions 600).\n")
    prompts = (
        ("base_url", "OmniRoute base URL (e.g. https://your-host/v1)", False),
        ("api_key", "API key (input is hidden)", True),
        ("model", "Model name", False),
    )
    try:
        for name, label, secret in prompts:
            have = getattr(current, name)
            if have and not force:
                continue
            ask = secret_fn if secret else input_fn
            while True:
                hint = f" [Enter keeps {_mask(have) if secret else have}]" if have else ""
                answer = ask(f"{label}{hint}: ").strip()
                if not answer:
                    if have:
                        break  # keep the current value; nothing to write
                    out("  This value is required.")
                    continue
                if name == "base_url":
                    try:
                        answer = _normalize_base_url(answer)
                    except ConfigError as exc:
                        out(f"  {exc}")
                        continue
                stored[name] = answer
                break
    except (EOFError, KeyboardInterrupt):
        raise ConfigError("Setup cancelled; nothing was saved.") from None
    write_config_values(stored, path)
    out(f"\nSaved to {path}.\n")
