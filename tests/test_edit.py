import unittest

from .helpers import WorkspaceTestCase

SRC = """import os


def add(a, b):
    return a + b


def sub(a, b):
    return a - b
"""


class EditFileTests(WorkspaceTestCase):
    def setUp(self):
        super().setUp()
        self.f = self.put("m.py", SRC)

    def edit(self, old, new, **kw):
        return self.call("edit_file", path="m.py", old_text=old, new_text=new, **kw)

    def test_unique_replacement_returns_diff_and_reports_change(self):
        r = self.edit("    return a + b", "    return a + b + 0")
        self.assertTrue(r.ok, r.output)
        self.assertEqual(r.changed, ["m.py"])
        self.assertIn("+    return a + b + 0", r.output)
        self.assertEqual(self.f.read_text(), SRC.replace("return a + b", "return a + b + 0"))

    def test_not_found_fails_loudly_and_leaves_file_untouched(self):
        r = self.edit("def multiply(a, b):", "def mul(a, b):")
        self.assertFalse(r.ok)
        self.assertIn("was not found", r.output)
        self.assertIn("No changes were made", r.output)
        self.assertEqual(self.f.read_text(), SRC)

    def test_ambiguous_match_lists_line_numbers(self):
        r = self.edit("(a, b):", "(x, y):")
        self.assertFalse(r.ok)
        self.assertIn("appears 2 times", r.output)
        self.assertIn("lines 4, 8", r.output)
        self.assertEqual(self.f.read_text(), SRC)

    def test_replace_all(self):
        r = self.edit("(a, b):", "(x, y):", replace_all=True)
        self.assertTrue(r.ok, r.output)
        self.assertIn("replaced 2 occurrences", r.output)
        self.assertEqual(self.f.read_text().count("(x, y):"), 2)

    def test_multiline_old_text_and_deletion(self):
        r = self.edit("\n\ndef sub(a, b):\n    return a - b\n", "")
        self.assertTrue(r.ok, r.output)
        self.assertNotIn("sub", self.f.read_text())

    def test_indentation_mismatch_gets_a_helpful_hint(self):
        r = self.call("edit_file", path="m.py", old_text="def sub(a, b):\nreturn a - b", new_text="x")
        self.assertFalse(r.ok)
        self.assertIn("different indentation", r.output)
        self.assertIn("lines 8-9", r.output)

    def test_near_miss_shows_closest_block(self):
        r = self.edit("def substract(a, b):\n    return a - b", "x")
        self.assertFalse(r.ok)
        self.assertIn("Closest match", r.output)
        self.assertIn("def sub(a, b):", r.output)

    def test_line_number_prefix_from_read_file_is_diagnosed(self):
        r = self.edit("     4\tdef add(a, b):\n     5\t    return a + b", "x")
        self.assertFalse(r.ok)
        self.assertIn("line-number prefixes", r.output)

    def test_no_op_and_empty_old_text_are_errors(self):
        self.assertIn("identical", self.edit("import os", "import os").output)
        self.assertIn("must not be empty", self.edit("", "x").output)

    def test_crlf_files_keep_their_line_endings(self):
        p = self.put("w.txt", "one\r\ntwo\r\nthree\r\n")
        r = self.call("edit_file", path="w.txt", old_text="one\ntwo", new_text="uno\ndos")
        self.assertTrue(r.ok, r.output)
        self.assertEqual(p.read_bytes(), b"uno\r\ndos\r\nthree\r\n")

    def test_lf_file_with_crlf_old_text(self):
        p = self.put("l.txt", "a\nb\n")
        self.assertTrue(self.call("edit_file", path="l.txt", old_text="a\r\nb", new_text="x\r\ny").ok)
        self.assertEqual(p.read_bytes(), b"x\ny\n")

    def test_refusals(self):
        self.put("bin.dat", b"\x00\x01")
        self.put(".git/config", "[core]\n")
        self.put("latin.txt", b"caf\xe9\n")
        for path, needle in (("bin.dat", "binary"), ("latin.txt", "not valid UTF-8"),
                             ("nope.py", "does not exist"), ("../x.py", "'..'"),
                             (".git/config", "protected")):
            r = self.call("edit_file", path=path, old_text="a", new_text="b")
            self.assertFalse(r.ok, path)
            self.assertIn(needle, r.output)
        self.assertEqual((self.ws / "latin.txt").read_bytes(), b"caf\xe9\n")

    def test_preview_matches_result_and_writes_nothing(self):
        diff = self.reg.preview("edit_file", {"path": "m.py", "old_text": "a - b", "new_text": "b - a"})
        self.assertIn("-    return a - b", diff)
        self.assertIn("+    return b - a", diff)
        self.assertEqual(self.f.read_text(), SRC)
        self.assertEqual(self.writes, [])

    def test_hook_called_once_per_successful_edit_only(self):
        self.edit("nothing here", "x")
        self.assertEqual(self.writes, [])
        self.edit("import os", "import sys")
        self.assertEqual(self.writes, [self.f])


class ApplyPatchTests(WorkspaceTestCase):
    def setUp(self):
        super().setUp()
        self.f = self.put("m.py", SRC)

    def patch(self, text, path="m.py"):
        return self.call("apply_patch", path=path, patch=text)

    def test_single_hunk_with_correct_numbers(self):
        r = self.patch("--- a/m.py\n+++ b/m.py\n@@ -4,3 +4,3 @@\n def add(a, b):\n-    return a + b\n+    return b + a\n \n")
        self.assertTrue(r.ok, r.output)
        self.assertIn("return b + a", self.f.read_text())
        self.assertEqual(self.f.read_text().count("\n"), SRC.count("\n"))

    def test_wrong_line_numbers_are_fine_when_content_is_unique(self):
        r = self.patch("@@ -99,2 +99,2 @@\n def sub(a, b):\n-    return a - b\n+    return a - b - 1\n")
        self.assertTrue(r.ok, r.output)
        self.assertIn("a - b - 1", self.f.read_text())

    def test_multiple_hunks_are_applied_together(self):
        r = self.patch("@@ -1,2 +1,2 @@\n-import os\n+import sys\n \n@@ -8,2 +8,3 @@\n def sub(a, b):\n     return a - b\n+    # done\n")
        self.assertTrue(r.ok, r.output)
        text = self.f.read_text()
        self.assertTrue(text.startswith("import sys\n"))
        self.assertTrue(text.endswith("    return a - b\n    # done\n"))

    def test_ambiguous_hunk_fails_unless_line_number_is_exact(self):
        body = "@@ -{n},2 +{n},2 @@\n-    return a + b\n+    return 1\n"
        r = self.call("apply_patch", path="m.py", patch="@@ -1,1 +1,1 @@\n-def f(a, b):\n+x\n")
        self.assertFalse(r.ok)
        self.put("dup.py", "x = 1\ny = 2\nx = 1\n")
        r = self.call("apply_patch", path="dup.py", patch="@@ -1,1 +1,1 @@\n-x = 1\n+x = 9\n")
        self.assertTrue(r.ok, r.output)          # header says line 1 -> disambiguates exactly
        self.assertEqual((self.ws / "dup.py").read_text(), "x = 9\ny = 2\nx = 1\n")
        self.put("dup2.py", "x = 1\ny = 2\nx = 1\n")
        r = self.call("apply_patch", path="dup2.py", patch="@@ -50,1 +50,1 @@\n-x = 1\n+x = 9\n")
        self.assertFalse(r.ok)
        self.assertIn("matches 2 places", r.output)
        self.assertEqual((self.ws / "dup2.py").read_text(), "x = 1\ny = 2\nx = 1\n")

    def test_all_or_nothing_when_a_later_hunk_fails(self):
        r = self.patch("@@ -1,1 +1,1 @@\n-import os\n+import sys\n@@ -8,1 +8,1 @@\n-nonexistent line\n+x\n")
        self.assertFalse(r.ok)
        self.assertIn("Hunk 2 does not match", r.output)
        self.assertIn("No changes were made", r.output)
        self.assertEqual(self.f.read_text(), SRC)

    def test_trailing_whitespace_difference_is_tolerated_and_context_preserved(self):
        p = self.put("ws.txt", "keep  \nchange me\nend\n")
        r = self.call("apply_patch", path="ws.txt", patch="@@ -1,3 +1,3 @@\n keep\n-change me\n+changed\n end\n")
        self.assertTrue(r.ok, r.output)
        self.assertEqual(p.read_text(), "keep  \nchanged\nend\n")   # context line kept as in file

    def test_add_and_delete_lines_and_blank_context(self):
        p = self.put("n.txt", "a\n\nb\n")
        r = self.call("apply_patch", path="n.txt", patch="@@ -1,3 +1,4 @@\n a\n\n-b\n+B\n+c\n")
        self.assertTrue(r.ok, r.output)
        self.assertEqual(p.read_text(), "a\n\nB\nc\n")

    def test_crlf_file_stays_crlf(self):
        p = self.put("c.txt", "a\r\nb\r\n")
        r = self.call("apply_patch", path="c.txt", patch="@@ -1,2 +1,2 @@\n a\n-b\n+B\n")
        self.assertTrue(r.ok, r.output)
        self.assertEqual(p.read_bytes(), b"a\r\nB\r\n")

    def test_code_fences_and_headers_are_tolerated(self):
        r = self.patch("```diff\ndiff --git a/m.py b/m.py\n--- a/m.py\n+++ b/m.py\n@@ -1,1 +1,1 @@\n-import os\n+import sys\n```")
        self.assertTrue(r.ok, r.output)

    def test_insert_into_empty_file(self):
        p = self.put("empty.txt", "")
        r = self.call("apply_patch", path="empty.txt", patch="@@ -0,0 +1,2 @@\n+one\n+two\n")
        self.assertTrue(r.ok, r.output)
        self.assertEqual(p.read_text(), "one\ntwo\n")

    def test_malformed_patches(self):
        for text, needle in (("just text", "No hunks"), ("@@ -1 +1 @@\n context only\n", "changes nothing"),
                             ("@@ -1 +1 @@\n?bad\n", "Malformed"),
                             ("@@ -1 +1 @@\n-a\n+b\ndiff --git a/x b/x\n", "one file per call")):
            r = self.patch(text)
            self.assertFalse(r.ok, text)
            self.assertIn(needle, r.output)
        self.assertEqual(self.f.read_text(), SRC)

    def test_preview_and_hook(self):
        diff = self.reg.preview("apply_patch", {"path": "m.py", "patch": "@@ -1,1 +1,1 @@\n-import os\n+import sys\n"})
        self.assertIn("+import sys", diff)
        self.assertEqual(self.f.read_text(), SRC)
        self.assertEqual(self.writes, [])


if __name__ == "__main__":
    unittest.main()
