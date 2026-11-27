import os
import tempfile
import unittest
from pathlib import Path

from aide.tools.errors import SandboxError, ToolError
from aide.tools.sandbox import Sandbox


class SandboxTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(os.path.realpath(self._tmp.name))
        self.ws = base / "proj"
        self.ws.mkdir()
        self.outside = base / "outside"
        self.outside.mkdir()
        (self.outside / "secret.txt").write_text("top secret")
        (self.ws / "a.txt").write_text("a")
        (self.ws / "sub").mkdir()
        self.sb = Sandbox(self.ws)

    def rejects(self, path, **kw):
        with self.assertRaises(SandboxError, msg=path):
            self.sb.resolve(path, **kw)

    # --- traversal ---------------------------------------------------------
    def test_dotdot_is_rejected_even_when_it_stays_inside(self):
        for p in ("..", "../outside/secret.txt", "sub/../a.txt", "sub/../../outside", "a/b/../../.."):
            self.rejects(p)

    def test_dots_inside_names_are_fine(self):
        (self.ws / "...").write_text("x")
        (self.ws / "a..b").write_text("x")
        self.assertEqual(self.sb.resolve("..."), self.ws / "...")
        self.assertEqual(self.sb.resolve("a..b"), self.ws / "a..b")

    # --- absolute paths ----------------------------------------------------
    def test_absolute_outside_is_rejected(self):
        self.rejects("/etc/passwd")
        self.rejects(str(self.outside / "secret.txt"))

    def test_absolute_inside_is_allowed(self):
        self.assertEqual(self.sb.resolve(str(self.ws / "a.txt"), must_exist=True), self.ws / "a.txt")

    def test_sibling_with_same_prefix_is_not_inside(self):
        evil = self.ws.parent / "proj-evil"
        evil.mkdir()
        self.rejects(str(evil / "x.txt"))

    # --- symlinks ----------------------------------------------------------
    def test_symlink_file_to_outside_is_rejected(self):
        os.symlink(self.outside / "secret.txt", self.ws / "link.txt")
        self.rejects("link.txt")
        self.rejects("link.txt", must_exist=True)

    def test_symlink_dir_to_outside_blocks_paths_through_it(self):
        os.symlink(self.outside, self.ws / "linkdir")
        self.rejects("linkdir")
        self.rejects("linkdir/secret.txt")
        self.rejects("linkdir/new_file.txt")  # writing a new file through it

    def test_dangling_symlink_to_outside_is_rejected_for_writes(self):
        os.symlink(self.outside / "does_not_exist.txt", self.ws / "dangling")
        self.rejects("dangling")

    def test_relative_symlink_escape_is_rejected(self):
        os.symlink("../outside/secret.txt", self.ws / "rel_link")
        self.rejects("rel_link")

    def test_symlink_inside_workspace_is_fine(self):
        os.symlink(self.ws / "a.txt", self.ws / "alias.txt")
        self.assertEqual(self.sb.resolve("alias.txt", must_exist=True), self.ws / "a.txt")

    def test_symlink_chain_that_ends_outside_is_rejected(self):
        os.symlink(self.outside, self.ws / "hop2")
        os.symlink(self.ws / "hop2", self.ws / "hop1")
        self.rejects("hop1/secret.txt")

    def test_workspace_given_as_symlink_is_resolved(self):
        link = self.ws.parent / "ws-link"
        os.symlink(self.ws, link)
        sb = Sandbox(link)
        self.assertEqual(sb.root, self.ws)
        self.assertEqual(sb.resolve("a.txt"), self.ws / "a.txt")

    # --- odd input ---------------------------------------------------------
    def test_bad_input(self):
        for p in ("", "   ", "a\x00b", "~", "~/x", "~root/x"):
            self.rejects(p)
        with self.assertRaises(SandboxError):
            self.sb.resolve(None)  # type: ignore[arg-type]

    def test_root_and_new_paths(self):
        self.assertEqual(self.sb.resolve("."), self.ws)
        self.assertEqual(self.sb.resolve("new/dir/file.txt"), self.ws / "new/dir/file.txt")
        self.assertEqual(self.sb.relative(self.ws), ".")
        self.assertEqual(self.sb.relative(self.ws / "sub"), "sub")

    # --- existence and protection -----------------------------------------
    def test_missing_file_suggests_similar_names(self):
        with self.assertRaises(ToolError) as ctx:
            self.sb.resolve("a.tx", must_exist=True)
        self.assertIn("does not exist", str(ctx.exception))
        self.assertIn("a.txt", str(ctx.exception))

    def test_git_dir_and_root_are_not_writable(self):
        (self.ws / ".git").mkdir()
        for p in (".git/config", ".git/hooks/pre-commit", ".git"):
            with self.assertRaises(SandboxError):
                self.sb.check_writable(self.sb.resolve(p))
        with self.assertRaises(SandboxError):
            self.sb.check_writable(self.sb.resolve("."))
        self.sb.check_writable(self.sb.resolve("sub/ok.txt"))  # normal paths pass


if __name__ == "__main__":
    unittest.main()
