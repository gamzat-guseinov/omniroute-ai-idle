import os
import tempfile
import unittest
from pathlib import Path

from aide.tools import create_registry


class WorkspaceTestCase(unittest.TestCase):
    """Creates a temp workspace plus a sibling 'outside' folder, and a tool registry."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(os.path.realpath(self._tmp.name))
        self.ws = base / "proj"
        self.ws.mkdir()
        self.outside = base / "outside"
        self.outside.mkdir()
        self.writes: list[Path] = []
        self.reg = create_registry(self.ws, before_write=self.writes.append)

    def put(self, rel: str, content: str | bytes = "x") -> Path:
        p = self.ws / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            p.write_bytes(content)
        else:
            p.write_bytes(content.encode())
        return p

    def call(self, name, **args):
        return self.reg.execute(name, args)

    def ok(self, name, **args):
        r = self.call(name, **args)
        self.assertTrue(r.ok, r.output)
        return r

    def fails(self, name, **args):
        r = self.call(name, **args)
        self.assertFalse(r.ok, r.output)
        return r.output
