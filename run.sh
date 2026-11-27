#!/usr/bin/env bash
# Launcher: creates a private virtualenv on first run, then starts aide.
# Usage: ./run.sh [workspace-dir] [options]     (see ./run.sh --help)
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
venv="$here/.venv"

if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    echo "aide needs Python 3.11 or newer (found: $(python3 --version 2>&1))." >&2
    exit 1
fi

# Reinstall automatically whenever pyproject.toml (i.e. the dependency list) changes.
stamp="$venv/.installed-$(md5sum "$here/pyproject.toml" | cut -c1-12)"
if [ ! -f "$stamp" ]; then
    echo "aide: installing dependencies (first run only)..." >&2
    [ -x "$venv/bin/python" ] || python3 -m venv "$venv"
    "$venv/bin/pip" install --quiet --disable-pip-version-check -e "$here"
    rm -f "$venv"/.installed-*
    touch "$stamp"
fi

exec "$venv/bin/python" -m aide "$@"
