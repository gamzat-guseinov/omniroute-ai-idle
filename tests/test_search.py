import os
import unittest

from .helpers import WorkspaceTestCase


class SearchTests(WorkspaceTestCase):
    def setUp(self):
        super().setUp()
        self.put(".gitignore", "*.log\nbuild/\n")
        self.put("src/app.py", "import os\n\ndef main():\n    print('TODO: fix')\n    return 1\n")
        self.put("src/util.py", "def helper():\n    # todo later\n    pass\n")
        self.put("web/index.ts", "const todo = 1;\n")
        self.put("notes.md", "TODO list\n")
        self.put("debug.log", "TODO in log\n")
        self.put("build/out.py", "TODO built\n")
        self.put("node_modules/x/i.js", "TODO dep\n")
        self.put("bin.dat", b"\x00TODO\x00")

    def test_basic_regex_and_output_format(self):
        out = self.ok("search", pattern=r"TODO").output
        self.assertIn("src/app.py:4:", out)
        self.assertIn("notes.md:1:TODO list", out)
        for skipped in ("debug.log", "build/out.py", "node_modules", "bin.dat"):
            self.assertNotIn(skipped, out.split("Format:")[1])

    def test_ignore_case_and_literal(self):
        out = self.ok("search", pattern="todo", ignore_case=True).output
        self.assertIn("src/util.py:2:", out)
        self.assertIn("src/app.py:4:", out)
        cs = self.ok("search", pattern="todo").output          # case-sensitive: skips TODO
        self.assertIn("src/util.py:2:", cs)
        self.assertNotIn("src/app.py", cs)
        self.assertNotIn("notes.md", cs)
        out = self.ok("search", pattern="print(", literal=True).output
        self.assertIn("src/app.py:4:", out)
        self.assertIn("Invalid regular expression", self.fails("search", pattern="print("))

    def test_glob_filters(self):
        out = self.ok("search", pattern="TODO|todo", glob="*.py").output
        self.assertIn("src/app.py", out)
        self.assertNotIn("notes.md", out)
        self.assertNotIn("index.ts", out)
        out = self.ok("search", pattern="todo", glob="*.{ts,md}").output
        self.assertIn("web/index.ts", out)
        out = self.ok("search", pattern="TODO", glob="src/**/*.py").output
        self.assertIn("src/app.py", out)

    def test_path_scoping_and_single_file(self):
        out = self.ok("search", pattern="TODO", path="src").output
        self.assertIn("src/app.py:4", out)
        self.assertNotIn("notes.md", out)
        out = self.ok("search", pattern="import", path="src/app.py").output
        self.assertIn("src/app.py:1:import os", out)

    def test_context_lines(self):
        out = self.ok("search", pattern="def main", context=1).output
        self.assertIn("src/app.py:3:def main():", out)
        self.assertIn("src/app.py-2-", out)
        self.assertIn("src/app.py-4-", out)

    def test_max_results_and_files_only(self):
        for i in range(10):
            self.put(f"many/f{i}.txt", "hit\nhit\n")
        out = self.ok("search", pattern="hit", path="many", max_results=3).output
        self.assertIn("stopped after 3 matches", out)
        self.assertEqual(sum(1 for ln in out.splitlines() if ln.startswith("many/")), 3)
        out = self.ok("search", pattern="hit", path="many", files_only=True).output
        self.assertIn("many/f0.txt  (2 matches)", out)

    def test_include_ignored(self):
        out = self.ok("search", pattern="TODO", include_ignored=True).output
        self.assertIn("debug.log", out)
        self.assertIn("build/out.py", out)
        self.assertIn("node_modules/x/i.js", out)

    def test_no_matches_message_mentions_gitignore(self):
        out = self.ok("search", pattern="zzz_not_there").output
        self.assertIn("No matches", out)
        self.assertIn("include_ignored", out)

    def test_symlinks_are_not_followed(self):
        (self.outside / "s.txt").write_text("TODO outside")
        os.symlink(self.outside, self.ws / "linkdir")
        os.symlink(self.outside / "s.txt", self.ws / "flink.txt")
        out = self.ok("search", pattern="TODO outside").output
        self.assertIn("No matches", out)
        self.assertIn("outside the workspace", self.fails("search", pattern="x", path=str(self.outside)))

    def test_oversized_and_crlf_files(self):
        self.put("huge.txt", "TODO\n" * 1_000_000)   # ~5 MB > 2 MB limit
        out = self.ok("search", pattern="TODO", path=".", glob="huge.txt").output
        self.assertIn("No matches", out)
        self.put("crlf.txt", "alpha\r\nbeta\r\n")
        out = self.ok("search", pattern="^beta$").output
        self.assertIn("crlf.txt:2:beta", out)

    def test_very_long_lines_are_clipped_in_output(self):
        self.put("wide.txt", "needle " + "x" * 5000 + "\n")
        out = self.ok("search", pattern="needle").output
        self.assertLess(len(out), 700)


if __name__ == "__main__":
    unittest.main()
