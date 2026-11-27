"""Manual harness: run a tool without a model.

    python -m aide.tools list
    python -m aide.tools call read_file '{"path": "app.py"}' -w /path/to/workspace
    python -m aide.tools call edit_file '{...}' --preview     # show the diff, change nothing
"""
from __future__ import annotations

import argparse
import json
import sys

from .registry import create_registry


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m aide.tools", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-w", "--workspace", default=".", help="workspace directory (default: .)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="list tools and their arguments")
    c = sub.add_parser("call", help="call one tool")
    c.add_argument("tool")
    c.add_argument("args", nargs="?", default="{}", help="JSON object with the arguments")
    c.add_argument("--preview", action="store_true", help="show the diff instead of applying")
    ns = p.parse_args(argv)

    registry = create_registry(ns.workspace)
    if ns.cmd == "list":
        for tool in registry.schemas():
            fn = tool["function"]
            props = ", ".join(fn["parameters"].get("properties", {}))
            print(f"{fn['name']}({props})")
        return 0
    try:
        args = json.loads(ns.args)
    except ValueError as exc:
        print(f"Arguments are not valid JSON: {exc}", file=sys.stderr)
        return 2
    if ns.preview:
        from .errors import ToolError
        try:
            text = registry.preview(ns.tool, args)
        except ToolError as exc:
            print(f"ERROR: {exc}")
            return 1
        print(text if text is not None else "(this tool has no preview)")
        return 0
    result = registry.execute(ns.tool, args)
    print(result.output if result.ok else f"ERROR: {result.output}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
