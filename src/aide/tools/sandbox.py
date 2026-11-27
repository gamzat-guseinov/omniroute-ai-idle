"""Workspace sandbox: every path a tool touches is validated here first.

Rules
-----
* The workspace root is resolved once (symlinks followed). Everything must stay inside it.
* Any '..' component is rejected outright, even if it would stay inside.
* Absolute paths are allowed only if they point inside the workspace.
* Paths are resolved with symlinks followed (also for files that do not exist yet), and the
  *resolved* location must be inside the workspace. A symlink that leads outside is therefore
  rejected, whether it is the file itself, a parent directory, or a dangling link.
* Containment is checked path-component-wise (so /work/proj-evil is NOT inside /work/proj).

Callers must use the returned resolved path for the actual I/O. This narrows, but cannot fully
close, the window in which another process could swap a symlink after the check; the sandbox
protects against a misbehaving model, not against a hostile local user.
"""
from __future__ import annotations

import difflib
import os
from pathlib import Path

from .errors import SandboxError, ToolError


class Sandbox:
    def __init__(self, root: str | os.PathLike[str]) -> None:
        real = Path(os.path.realpath(Path(root).expanduser()))
        if not real.is_dir():
            raise SandboxError(f"Workspace {real} is not a directory.")
        self.root = real

    def resolve(self, path: str, *, must_exist: bool = False) -> Path:
        """Validate `path` and return its resolved absolute location inside the workspace."""
        if not isinstance(path, str) or not path.strip():
            raise SandboxError("Path is empty. Give a path relative to the workspace root "
                               "(use '.' for the root itself).")
        if "\x00" in path:
            raise SandboxError("Path contains a NUL character.")
        if path.startswith("~"):
            raise SandboxError("'~' is not expanded. Use a path relative to the workspace root.")
        given = Path(path)
        if ".." in given.parts:
            raise SandboxError(f"Path '{path}' contains '..', which is not allowed. "
                               f"Use a path inside the workspace without '..'.")
        candidate = given if given.is_absolute() else self.root / given
        real = Path(os.path.realpath(candidate))
        if not real.is_relative_to(self.root):
            raise SandboxError(
                f"Path '{path}' is outside the workspace ({self.root}). Only paths inside the "
                f"workspace are allowed; absolute paths elsewhere and symlinks leading out of "
                f"it are blocked.")
        if must_exist and not real.exists():
            raise ToolError(self._missing_message(path, real))
        return real

    def relative(self, real: Path) -> str:
        """Workspace-relative POSIX path for display ('.' for the root)."""
        return real.relative_to(self.root).as_posix()

    def check_writable(self, real: Path) -> None:
        rel = real.relative_to(self.root)
        if not rel.parts:
            raise SandboxError("The workspace root itself cannot be modified.")
        if ".git" in rel.parts:
            raise SandboxError("Files inside .git are protected and cannot be modified by tools.")

    def _missing_message(self, given: str, real: Path) -> str:
        msg = f"'{given}' does not exist."
        parent = real.parent
        if parent.is_dir():
            try:
                near = difflib.get_close_matches(real.name, os.listdir(parent), n=3, cutoff=0.6)
            except OSError:
                near = []
            if near:
                shown = ", ".join(self.relative(parent / n) for n in near)
                msg += f" Did you mean: {shown}?"
        elif not real.parent.exists():
            msg += " Its parent directory does not exist either; use list_dir to explore."
        return msg
