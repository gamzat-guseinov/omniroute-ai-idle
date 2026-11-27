import json
import unittest

from aide.tools import Limits, Tool, ToolResult, create_registry, validate_args
from aide.tools.errors import ToolError
from .helpers import WorkspaceTestCase


class SchemaTests(WorkspaceTestCase):
    def test_schemas_are_valid_openai_function_definitions(self):
        schemas = self.reg.schemas()
        self.assertEqual([s["function"]["name"] for s in schemas],
                         ["list_dir", "tree", "read_file", "write_file", "search", "edit_file", "apply_patch"])
        for s in schemas:
            json.dumps(s)  # serializable
            fn = s["function"]
            self.assertEqual(s["type"], "function")
            self.assertTrue(fn["description"])
            self.assertEqual(fn["parameters"]["type"], "object")
            for req in fn["parameters"].get("required", []):
                self.assertIn(req, fn["parameters"]["properties"])
            for spec in fn["parameters"]["properties"].values():
                self.assertIn(spec["type"], ("string", "integer", "boolean", "array"))

    def test_write_tools_are_marked_as_writes(self):
        kinds = {n: self.reg.get(n).kind for n in self.reg.names()}
        self.assertEqual({n for n, k in kinds.items() if k == "write"},
                         {"write_file", "edit_file", "apply_patch"})


class ValidationTests(WorkspaceTestCase):
    def test_coercion_of_common_model_slips(self):
        self.put("a.txt", "1\n2\n3\n")
        out = self.ok("read_file", path="a.txt", start_line="2", end_line=3.0).output
        self.assertIn("lines 2-3 of 3", out)
        self.ok("search", pattern="1", literal="true", ignore_case="False")

    def test_nulls_count_as_missing_optional_args(self):
        self.put("a.txt", "x\n")
        self.ok("read_file", path="a.txt", start_line=None, end_line=None)

    def test_errors_are_specific(self):
        self.assertIn("Missing required argument(s): path", self.fails("read_file"))
        self.assertIn("Unknown argument(s): foo", self.fails("read_file", path="a", foo=1))
        self.assertIn("must be an integer", self.fails("read_file", path="a", start_line="abc"))
        self.assertIn("must be at least 1", self.fails("read_file", path="a", start_line=0))
        self.assertIn("must be a string", self.fails("write_file", path="a", content=5))
        self.assertIn("must be true or false", self.fails("search", pattern="x", literal="maybe"))

    def test_maximum_is_clamped_not_rejected(self):
        self.put("a.txt", "hit\n")
        self.ok("search", pattern="hit", max_results=100000)

    def test_arguments_must_be_an_object(self):
        r = self.reg.execute("read_file", ["a.txt"])
        self.assertFalse(r.ok)
        self.assertIn("JSON object", r.output)

    def test_validate_args_directly(self):
        schema = {"type": "object", "properties": {"m": {"type": "string", "enum": ["a", "b"]}}}
        self.assertEqual(validate_args(schema, {"m": "a"}), {"m": "a"})
        with self.assertRaises(ToolError):
            validate_args(schema, {"m": "c"})


class ExecutionTests(WorkspaceTestCase):
    def test_unknown_tool(self):
        r = self.reg.execute("rm_rf", {})
        self.assertFalse(r.ok)
        self.assertIn("Unknown tool 'rm_rf'", r.output)
        self.assertIn("read_file", r.output)

    def test_unexpected_exceptions_become_results(self):
        def boom(ctx, args):
            raise RuntimeError("kaboom")
        self.reg.register(Tool("boom", "d", {"type": "object", "properties": {}}, boom))
        r = self.reg.execute("boom", {})
        self.assertFalse(r.ok)
        self.assertIn("Internal error in boom: RuntimeError: kaboom", r.output)

    def test_huge_output_is_truncated_in_the_middle(self):
        reg = create_registry(self.ws, limits=Limits(max_output_chars=1000, max_read_lines=100000))
        (self.ws / "big.txt").write_text("\n".join(f"line {i}" for i in range(5000)) + "\n")
        r = reg.execute("read_file", {"path": "big.txt"})
        self.assertTrue(r.ok)
        self.assertLess(len(r.output), 1200)
        self.assertIn("characters omitted", r.output)
        self.assertIn("line 4999", r.output)   # the end survives

    def test_preview_returns_none_for_read_tools_and_raises_for_bad_calls(self):
        self.assertIsNone(self.reg.preview("read_file", {"path": "x"}))
        self.assertIsNone(self.reg.preview("nonexistent", {}))
        with self.assertRaises(ToolError):
            self.reg.preview("edit_file", {"path": "missing.py", "old_text": "a", "new_text": "b"})

    def test_end_to_end_agent_style_session(self):
        """What the agent loop will do: explore, edit, verify."""
        self.ok("write_file", path="app.py", content="def greet():\n    return 'helo'\n")
        self.assertIn("app.py", self.ok("tree").output)
        self.assertIn("app.py:2:", self.ok("search", pattern="helo").output)
        self.assertIn("2\t", self.ok("read_file", path="app.py").output)
        self.ok("edit_file", path="app.py", old_text="'helo'", new_text="'hello'")
        self.assertIn("hello", (self.ws / "app.py").read_text())
        self.assertEqual(len(self.writes), 2)


if __name__ == "__main__":
    unittest.main()
