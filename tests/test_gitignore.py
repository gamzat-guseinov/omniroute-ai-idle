import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from aide.tools.gitignore import GitIgnore, compile_glob

GITIGNORE = r"""
# comment
*.log
!keep.log
build/
/root_only.txt
docs/*.tmp
**/cache
src/**/gen
temp?
[ab]file.txt
\#hash.txt
*.py[co]
!/src/special.pyc
logs/
!logs/important.txt
nested/**/deep.txt
abc/**
!abc/keepme.txt
/*.bak
data/
!data/
"""

SUB_GITIGNORE = "*.txt\n!important.txt\n/only_here.md\n"

FILES = [
    "a.log", "keep.log", "x/y.log", "build/out.o", "src/build/x.o", "root_only.txt",
    "sub/root_only.txt", "docs/a.tmp", "docs/deep/b.tmp", "cache", "x/cache/f", "src/gen",
    "src/a/b/gen", "gen", "temp1", "temp", "temp12", "afile.txt", "cfile.txt", "#hash.txt",
    "m.pyc", "m.pyo", "m.pyx", "src/special.pyc", "src/other.pyc", "logs/a.txt",
    "logs/important.txt", "nested/deep.txt", "nested/a/b/deep.txt", "nested/other.txt",
    "abc/x", "abc/keepme.txt", "abc/d/e", "old.bak", "sub2/old.bak", "data/f",
    "plain.py", "README.md",
    "sub/.gitignore", "sub/a.txt", "sub/important.txt", "sub/only_here.md",
    "sub/deeper/b.txt", "sub/deeper/only_here.md", "other/a.txt",
]


class GitIgnoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(os.path.realpath(self._tmp.name))
        (self.root / ".gitignore").write_text(GITIGNORE)
        for f in FILES:
            p = self.root / f
            p.parent.mkdir(parents=True, exist_ok=True)
            if f == "sub/.gitignore":
                p.write_text(SUB_GITIGNORE)
            elif not p.exists():
                p.write_text("x")
        self.gi = GitIgnore(self.root)

    def all_paths(self):
        for dirpath, dirnames, filenames in os.walk(self.root):
            if ".git" in dirnames:
                dirnames.remove(".git")
            for name in dirnames:
                yield (Path(dirpath) / name).relative_to(self.root).as_posix(), True
            for name in filenames:
                yield (Path(dirpath) / name).relative_to(self.root).as_posix(), False

    @unittest.skipUnless(shutil.which("git"), "git is not installed")
    def test_agrees_with_real_git_on_every_path(self):
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        self.gi.begin()
        mismatches = []
        for rel, is_dir in sorted(self.all_paths()):
            proc = subprocess.run(["git", "check-ignore", "-q", "--", rel], cwd=self.root)
            git_says = proc.returncode == 0
            ours = self.gi.is_ignored(rel, is_dir)
            if git_says != ours:
                mismatches.append(f"{rel} (dir={is_dir}): git={git_says} ours={ours}")
        self.assertEqual(mismatches, [])

    def test_key_rules_without_git(self):
        gi = self.gi
        cases = {
            ("a.log", False): True, ("keep.log", False): False, ("build", True): True,
            ("build/out.o", False): True, ("root_only.txt", False): True,
            ("other/root_only.txt", False): False, ("docs/a.tmp", False): True,
            ("docs/deep/b.tmp", False): False, ("logs/important.txt", False): True,  # parent ignored
            ("plain.py", False): False, ("sub/a.txt", False): True,
            ("sub/important.txt", False): False, ("other/a.txt", False): False,
        }
        for (rel, is_dir), expected in cases.items():
            self.assertEqual(gi.is_ignored(rel, is_dir), expected, rel)

    def test_skip_hides_git_and_noise_but_include_ignored_shows_the_rest(self):
        (self.root / "node_modules").mkdir()
        self.assertTrue(self.gi.skip("node_modules", True))
        self.assertFalse(self.gi.skip("node_modules", True, include_ignored=True))
        self.assertTrue(self.gi.skip(".git", True, include_ignored=True))
        self.assertTrue(self.gi.skip(".git/config", False, include_ignored=True))
        self.assertTrue(self.gi.skip("a.log", False))
        self.assertFalse(self.gi.skip("a.log", False, include_ignored=True))

    def test_gitignore_edits_are_noticed_after_begin(self):
        self.assertFalse(self.gi.is_ignored("plain.py", False))
        (self.root / ".gitignore").write_text("*.py\n")
        self.gi.begin()
        self.assertTrue(self.gi.is_ignored("plain.py", False))

    def test_git_info_exclude_is_read(self):
        (self.root / ".git" / "info").mkdir(parents=True)
        (self.root / ".git" / "info" / "exclude").write_text("*.secret\n")
        self.gi.begin()
        self.assertTrue(self.gi.is_ignored("a/b.secret", False))


class GlobTests(unittest.TestCase):
    def test_globs(self):
        m = compile_glob("*.py")
        self.assertTrue(m("a.py") and m("src/deep/a.py"))
        self.assertFalse(m("a.pyc") or m("a.txt"))
        m = compile_glob("src/**/*.ts")
        self.assertTrue(m("src/a.ts") and m("src/x/y/a.ts"))
        self.assertFalse(m("lib/a.ts") or m("other/src/a.ts"))
        m = compile_glob("*.{ts,tsx}")
        self.assertTrue(m("a.ts") and m("d/b.tsx"))
        self.assertFalse(m("a.js"))
        m = compile_glob("docs/*.md")
        self.assertTrue(m("docs/a.md"))
        self.assertFalse(m("docs/x/a.md"))


if __name__ == "__main__":
    unittest.main()
