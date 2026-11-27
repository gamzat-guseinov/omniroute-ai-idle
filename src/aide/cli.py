"""Command-line entry point: parse arguments, ensure configuration, start the UI."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import __version__
from .config import (
    ConfigError,
    config_path,
    load_config,
    missing_fields,
    run_wizard,
)
from .debuglog import DebugLog, default_log_path


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="aide",
        description="AI coding agent for the terminal, powered by the OmniRoute API.",
        epilog="Settings can also come from OMNIROUTE_BASE_URL, OMNIROUTE_API_KEY, "
               "OMNIROUTE_MODEL, OMNIROUTE_TEMPERATURE, OMNIROUTE_MAX_TOKENS, "
               "OMNIROUTE_TIMEOUT (environment variables override the config file).",
    )
    p.add_argument("workspace", nargs="?", default=".",
                   help="project directory the agent works in (default: current directory)")
    p.add_argument("--config", type=Path, metavar="FILE",
                   help=f"config file to use (default: {config_path()})")
    p.add_argument("--model", help="model name (overrides config)")
    p.add_argument("--temperature", type=float, help="sampling temperature (overrides config)")
    p.add_argument("--max-tokens", type=int, help="max tokens per reply (overrides config)")
    p.add_argument("--reconfigure", action="store_true",
                   help="run the interactive setup again")
    p.add_argument("--debug-log", action="store_true",
                   help=f"log API traffic (keys redacted) to {default_log_path()}")
    p.add_argument("--debug-log-file", type=Path, metavar="FILE",
                   help="like --debug-log, but write to FILE")
    p.add_argument("--version", action="version", version=f"aide {__version__}")
    return p


def resolve_workspace(arg: str) -> Path:
    path = Path(arg).expanduser().resolve()
    if not path.exists():
        raise ConfigError(f"Workspace {path} does not exist. Create it first: mkdir -p {path}")
    if not path.is_dir():
        raise ConfigError(f"Workspace {path} is not a directory.")
    if not os.access(path, os.R_OK | os.W_OK | os.X_OK):
        raise ConfigError(f"No read/write access to workspace {path}.")
    return path


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        workspace = resolve_workspace(args.workspace)
        path = args.config or config_path()
        overrides = {"model": args.model, "temperature": args.temperature,
                     "max_tokens": args.max_tokens}
        cfg = load_config(path, overrides=overrides)

        if args.reconfigure or missing_fields(cfg):
            if not sys.stdin.isatty():
                raise ConfigError(
                    "Configuration is incomplete and there is no terminal for setup. Set "
                    "OMNIROUTE_BASE_URL, OMNIROUTE_API_KEY and OMNIROUTE_MODEL, or run aide "
                    "interactively once."
                )
            run_wizard(path, cfg, force=args.reconfigure)
            cfg = load_config(path, overrides=overrides)
            if missing_fields(cfg):
                raise ConfigError(f"Still missing: {', '.join(missing_fields(cfg))}.")

        log_path = args.debug_log_file or (default_log_path() if args.debug_log else None)
        debug = DebugLog(log_path, secrets=[cfg.api_key])
    except ConfigError as exc:
        print(f"aide: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\naide: cancelled.", file=sys.stderr)
        return 130

    try:  # imported late so --help/--version/setup work even without the UI dependency
        from .ui.app import AideApp
    except ImportError as exc:
        print(f"aide: missing dependency '{exc.name}'. Install with: pip install -e .  "
              f"(or start via ./run.sh)", file=sys.stderr)
        return 1

    AideApp(cfg, workspace, debug).run()
    return 0
