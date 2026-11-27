"""A small, dependency-free implementation of .gitignore semantics.

Supported: nested .gitignore files (deeper files win, later lines win), .git/info/exclude,
negation (!), directory-only patterns (trailing /), anchored patterns (leading or inner /),
*, ?, [...] classes, ** in all its forms, backslash escapes, and the rule that nothing inside
an ignored directory can be re-included. The global core.excludesFile is not read.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

# Never shown by list_dir/tree/search. .git is hidden even with include_ignored.
ALWAYS_HIDDEN = frozenset({".git"})
# Noise that is skipped by default even without a .gitignore entry.
NOISE_NAMES = frozenset({
    "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache",
    ".tox", ".DS_Store",
})


def translate_glob(pat: str) -> str:
    """Translate one gitignore-style glob (without ! or trailing /) to a regex string."""
    i, n, out = 0, len(pat), []
    while i < n:
        c = pat[i]
        if c == "*":
            j = i
            while j < n and pat[j] == "*":
                j += 1
            if j - i >= 2:
                at_start = i == 0 or pat[i - 1] == "/"
                if at_start and j < n and pat[j] == "/":     # "**/"  -> zero or more dirs
                    out.append("(?:.*/)?")
                    i = j + 1
                    continue
                if at_start and j == n:                      # "**" or "/**" -> everything
                    out.append(".*")
                    i = j
                    continue
            out.append("[^/]*")
            i = j
        elif c == "?":
            out.append("[^/]")
            i += 1
        elif c == "[":
            j = i + 1
            if j < n and pat[j] in "!^":
                j += 1
            if j < n and pat[j] == "]":
                j += 1
            while j < n and pat[j] != "]":
                j += 1
            if j >= n:                                        # unclosed -> literal '['
                out.append(r"\[")
                i += 1
            else:
                body = pat[i + 1:j]
                negate = body[:1] in ("!", "^")
                if negate:
                    body = body[1:]
                body = body.replace("\\", "\\\\").replace("[", r"\[")
                out.append("[^/" + body + "]" if negate else "[" + body + "]")
                i = j + 1
        elif c == "\\" and i + 1 < n:
            out.append(re.escape(pat[i + 1]))
            i += 2
        else:
            out.append(re.escape(c))
            i += 1
    return "".join(out)


def _expand_braces(glob: str) -> list[str]:
    m = re.search(r"\{([^{}]*)\}", glob)
    if not m:
        return [glob]
    return [alt for part in m.group(1).split(",")
            for alt in _expand_braces(glob[:m.start()] + part + glob[m.end():])]


def compile_glob(glob: str) -> Callable[[str], bool]:
    """Matcher for the search tool's `glob` argument, applied to a path relative to the
    search root. Without '/', the pattern is matched against the file name at any depth."""
    matchers = []
    for g in _expand_braces(glob.strip()):
        g = g[2:] if g.startswith("./") else g
        anchored = "/" in g
        rx = re.compile(translate_glob(g.lstrip("/")))
        matchers.append((anchored, rx))

    def match(rel: str) -> bool:
        base = rel.rsplit("/", 1)[-1]
        return any(rx.fullmatch(rel if anchored else base) for anchored, rx in matchers)
    return match


@dataclass(frozen=True)
class _Rule:
    regex: re.Pattern[str]
    negated: bool
    dir_only: bool
    anchored: bool

    def matches(self, rel: str, is_dir: bool) -> bool:
        if self.dir_only and not is_dir:
            return False
        target = rel if self.anchored else rel.rsplit("/", 1)[-1]
        return self.regex.fullmatch(target) is not None


def parse_rule(line: str) -> _Rule | None:
    line = line.rstrip("\r\n")
    while line.endswith(" ") and not line.endswith("\\ "):    # trailing spaces are ignored
        line = line[:-1]
    if not line or line.startswith("#"):
        return None
    negated = False
    if line.startswith("!"):
        negated, line = True, line[1:]
    elif line.startswith(("\\!", "\\#")):
        line = line[1:]
    dir_only = line.endswith("/")
    if dir_only:
        line = line.rstrip("/")
    if not line:
        return None
    anchored = "/" in line
    line = line.lstrip("/")
    return _Rule(re.compile(translate_glob(line)), negated, dir_only, anchored)


class GitIgnore:
    """Answers "would git ignore this path?" for one workspace."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._cache: dict[str, tuple[tuple[int, int] | None, list[_Rule]]] = {}
        self._memo: dict[str, list[_Rule]] = {}

    def begin(self) -> None:
        """Call at the start of each tool run so edited .gitignore files are noticed."""
        self._memo.clear()

    def _load(self, key: str, path: Path) -> list[_Rule]:
        if key in self._memo:
            return self._memo[key]
        try:
            st = path.stat()
            sig: tuple[int, int] | None = (st.st_mtime_ns, st.st_size)
        except OSError:
            sig = None
        cached = self._cache.get(key)
        if cached and cached[0] == sig:
            rules = cached[1]
        else:
            rules = []
            if sig is not None:
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    text = ""
                rules = [r for r in map(parse_rule, text.splitlines()) if r]
            self._cache[key] = (sig, rules)
        self._memo[key] = rules
        return rules

    def _match(self, rel: str, is_dir: bool) -> bool:
        parts = rel.split("/")
        sets: list[tuple[str, list[_Rule]]] = [
            ("", self._load("\0exclude", self.root / ".git" / "info" / "exclude"))]
        for i in range(0, len(parts)):
            base = "/".join(parts[:i])
            gi = (self.root / base / ".gitignore") if base else (self.root / ".gitignore")
            sets.append((base, self._load(base, gi)))
        ignored = False
        for base, rules in sets:
            sub = rel if not base else rel[len(base) + 1:]
            for rule in rules:
                if rule.matches(sub, is_dir):
                    ignored = not rule.negated
        return ignored

    def is_ignored(self, rel: str, is_dir: bool, *, check_parents: bool = True) -> bool:
        """`rel` is a workspace-relative POSIX path."""
        if rel in ("", "."):
            return False
        parts = rel.split("/")
        if check_parents:
            for i in range(1, len(parts)):
                if self._match("/".join(parts[:i]), True):
                    return True
        return self._match(rel, is_dir)

    def skip(self, rel: str, is_dir: bool, *, include_ignored: bool = False) -> bool:
        """Should list/tree/search leave this entry out? (Parents are handled by pruning.)"""
        parts = rel.split("/")
        if any(p in ALWAYS_HIDDEN for p in parts):
            return True
        if include_ignored:
            return False
        if parts[-1] in NOISE_NAMES:
            return True
        return self.is_ignored(rel, is_dir, check_parents=False)


def scan_dir(path: os.PathLike[str] | str) -> list[os.DirEntry[str]]:
    """Directory entries sorted directories-first, then case-insensitively by name."""
    try:
        with os.scandir(path) as it:
            entries = list(it)
    except OSError:
        return []
    entries.sort(key=lambda e: (not e.is_dir(follow_symlinks=False), e.name.lower(), e.name))
    return entries
