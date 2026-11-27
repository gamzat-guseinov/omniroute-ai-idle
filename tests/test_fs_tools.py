import os
import stat
import unittest

from .helpers import WorkspaceTestCase


class ListAndTreeTests(WorkspaceTestCase):
    def setUp(self):
        super().setUp()
        self.put(".gitignore", "*.log\nbuild/\n")
        self.put("README.md", "hi")
        self.put("src/app.py", "print(1)\n")
        self.put("src/util/helpers.py", "x")
        self.put("debug.log")
        self.put("build/out.o")
        self.put("node_modules/lib/index.js")
        self.put("__pycache__/a.pyc")
        self.put(".git/config")

    def test_list_dir_orders_dirs_first_and_respects_gitignore(self):
        out = self.ok("list_dir").output
        lines = out.splitlines()
        self.assertIn("(3 entries)", lines[0])          # src/, .gitignore, README.md
        self.assertEqual(lines[1], "src/")
        for hidden in ("debug.log", "build", "node_modules", "__pycache__", "\n.git/"):
            self.assertNotIn(hidden, out)
        self.assertIn("README.md  (2 B)", out)

    def test_include_ignored_shows_ignored_but_never_git(self):
        out = self.ok("list_dir", include_ignored=True).output
        for shown in ("debug.log", "build/", "node_modules/"):
            self.assertIn(shown, out)
        self.assertNotIn(".git/", out)

    def test_list_dir_errors_and_truncation(self):
        self.assertIn("is a file", self.fails("list_dir", path="README.md"))
        self.assertIn("does not exist", self.fails("list_dir", path="nope"))
        for i in range(5):
            self.put(f"many/f{i}.txt")
        out = self.ok("list_dir", path="many", max_entries=2).output
        self.assertIn("3 more entries not shown", out)

    def test_tree_indents_and_limits_depth(self):
        out = self.ok("tree", max_depth=1).output
        self.assertIn("  src/", out)
        self.assertNotIn("app.py", out)
        out = self.ok("tree").output
        self.assertIn("    app.py", out)
        self.assertIn("      helpers.py", out)
        self.assertNotIn("node_modules", out)

    def test_tree_entry_limit(self):
        out = self.ok("tree", max_entries=2).output
        self.assertIn("more entries not shown", out)

    def test_symlinks_are_listed_but_not_followed(self):
        (self.outside / "secret.txt").write_text("s")
        os.symlink(self.outside, self.ws / "linkdir")
        out = self.ok("tree", include_ignored=True).output
        self.assertIn("linkdir@ ->", out)
        self.assertNotIn("secret.txt", out)

    def test_listing_outside_is_refused(self):
        self.assertIn("outside the workspace", self.fails("list_dir", path=str(self.outside)))


class ReadFileTests(WorkspaceTestCase):
    def test_numbered_output(self):
        self.put("a.txt", "one\ntwo\nthree\n")
        out = self.ok("read_file", path="a.txt").output
        self.assertIn("lines 1-3 of 3", out)
        self.assertIn("     2\ttwo", out)

    def test_line_range(self):
        self.put("a.txt", "\n".join(f"L{i}" for i in range(1, 11)) + "\n")
        out = self.ok("read_file", path="a.txt", start_line=4, end_line=6).output
        self.assertIn("lines 4-6 of 10", out)
        self.assertIn("L4", out)
        self.assertIn("L6", out)
        self.assertNotIn("L7", out)
        self.assertNotIn("more lines follow", out)  # explicit end_line reached

    def test_long_file_is_paged_with_continuation_hint(self):
        self.put("big.txt", "\n".join(f"line {i}" for i in range(1, 5001)) + "\n")
        out = self.ok("read_file", path="big.txt").output
        self.assertIn("lines 1-2000 of 5000", out)
        self.assertIn("start_line=2001", out)
        out = self.ok("read_file", path="big.txt", start_line=4990).output
        self.assertIn("line 5000", out)
        self.assertNotIn("more lines follow", out)

    def test_crlf_and_missing_final_newline(self):
        self.put("w.txt", "a\r\nb")
        out = self.ok("read_file", path="w.txt").output
        self.assertIn("     1\ta\n", out + "\n")
        self.assertNotIn("\r", out)
        self.assertIn("lines 1-2 of 2", out)

    def test_very_long_lines_are_cut(self):
        self.put("long.txt", "x" * 5000 + "\n")
        out = self.ok("read_file", path="long.txt").output
        self.assertIn("line cut, 5000 chars", out)
        self.assertLess(len(out), 2500)

    def test_empty_binary_and_bad_ranges(self):
        self.put("empty.txt", "")
        self.assertIn("empty file", self.ok("read_file", path="empty.txt").output)
        self.put("bin.dat", b"\x00\x01\x02binary")
        self.assertIn("binary file", self.fails("read_file", path="bin.dat"))
        self.put("a.txt", "one\n")
        self.assertIn("past the end", self.fails("read_file", path="a.txt", start_line=9))
        self.assertIn("end_line", self.fails("read_file", path="a.txt", start_line=3, end_line=2))

    def test_directory_missing_and_outside(self):
        self.put("d/x.txt")
        self.assertIn("is a directory", self.fails("read_file", path="d"))
        self.assertIn("does not exist", self.fails("read_file", path="zzz.txt"))
        (self.outside / "s.txt").write_text("secret")
        self.assertIn("outside the workspace", self.fails("read_file", path=str(self.outside / "s.txt")))
        os.symlink(self.outside / "s.txt", self.ws / "l.txt")
        self.assertIn("outside the workspace", self.fails("read_file", path="l.txt"))

    def test_invalid_utf8_is_shown_with_replacement(self):
        self.put("latin.txt", b"caf\xe9\n")
        self.assertIn("caf", self.ok("read_file", path="latin.txt").output)


class WriteFileTests(WorkspaceTestCase):
    def test_create_with_parent_dirs_and_overwrite(self):
        r = self.ok("write_file", path="a/b/c.txt", content="hello\nworld\n")
        self.assertIn("Created a/b/c.txt (2 lines", r.output)
        self.assertEqual(r.changed, ["a/b/c.txt"])
        self.assertEqual((self.ws / "a/b/c.txt").read_text(), "hello\nworld\n")
        r = self.ok("write_file", path="a/b/c.txt", content="new")
        self.assertIn("Overwrote", r.output)
        self.assertEqual((self.ws / "a/b/c.txt").read_text(), "new")

    def test_unicode_and_no_temp_files_left_behind(self):
        self.put("u.txt", "old")
        self.ok("write_file", path="u.txt", content="héllo 😀\n")
        self.assertEqual((self.ws / "u.txt").read_text(encoding="utf-8"), "héllo 😀\n")
        self.assertEqual([p.name for p in self.ws.iterdir()], ["u.txt"])

    def test_existing_file_mode_is_preserved(self):
        p = self.put("run.sh", "#!/bin/sh\n")
        os.chmod(p, 0o755)
        self.ok("write_file", path="run.sh", content="#!/bin/sh\necho hi\n")
        self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o755)

    def test_before_write_hook_sees_every_write(self):
        self.ok("write_file", path="one.txt", content="1")
        self.ok("write_file", path="one.txt", content="2")
        self.assertEqual(self.writes, [self.ws / "one.txt", self.ws / "one.txt"])

    def test_refusals_change_nothing(self):
        self.put("d/x.txt")
        self.assertIn("is a directory", self.fails("write_file", path="d", content="x"))
        self.assertIn("outside the workspace", self.fails("write_file", path="/tmp/aide-escape.txt", content="x"))
        self.assertIn("'..'", self.fails("write_file", path="../escape.txt", content="x"))
        self.assertIn("protected", self.fails("write_file", path=".git/config", content="x"))
        self.assertIn("workspace root", self.fails("write_file", path=".", content="x"))
        self.assertFalse((self.outside / "escape.txt").exists())
        self.assertEqual(self.writes, [])

    def test_cannot_write_through_escaping_symlinks(self):
        os.symlink(self.outside, self.ws / "linkdir")
        self.assertIn("outside the workspace", self.fails("write_file", path="linkdir/new.txt", content="x"))
        self.assertEqual(list(self.outside.iterdir()), [])
        (self.outside / "victim.txt").write_text("keep")
        os.symlink(self.outside / "victim.txt", self.ws / "victim.txt")
        self.fails("write_file", path="victim.txt", content="pwned")
        self.assertEqual((self.outside / "victim.txt").read_text(), "keep")

    def test_parent_that_is_a_file(self):
        self.put("f.txt")
        self.assertIn("Cannot write", self.fails("write_file", path="f.txt/inner.txt", content="x"))

    def test_preview_shows_diff_and_changes_nothing(self):
        self.put("p.txt", "a\nb\n")
        diff = self.reg.preview("write_file", {"path": "p.txt", "content": "a\nc\n"})
        self.assertIn("-b", diff)
        self.assertIn("+c", diff)
        self.assertEqual((self.ws / "p.txt").read_text(), "a\nb\n")
        new = self.reg.preview("write_file", {"path": "brand_new.txt", "content": "x\n"})
        self.assertIn("--- /dev/null", new)
        self.assertFalse((self.ws / "brand_new.txt").exists())


if __name__ == "__main__":
    unittest.main()
