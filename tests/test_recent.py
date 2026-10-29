"""The recent projects list (settings.recent_projects / note_recent) and the editor's start project (cli gui)."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jfgstudio import cli, settings


class RecentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"JFGSTUDIO_CONFIG_DIR": str(Path(self.tmp.name) / "config")})
        patcher.start()
        self.addCleanup(patcher.stop)

    def project(self, name):
        p = Path(self.tmp.name) / f"{name}.jfgproj"
        p.mkdir()
        return str(p.resolve())

    def test_last_first_no_doubles_gone_dropped(self):
        a, b = self.project("a"), self.project("b")
        self.assertEqual(settings.recent_projects(), [])
        settings.note_recent(a)
        settings.note_recent(b)
        settings.note_recent(a)
        self.assertEqual(settings.recent_projects(), [a, b])
        os.rmdir(b)
        self.assertEqual(settings.recent_projects(), [a])

    def test_capped(self):
        for k in range(settings.RECENT_MAX + 3):
            settings.note_recent(self.project(f"p{k}"))
        recent = settings.recent_projects()
        self.assertEqual(len(recent), settings.RECENT_MAX)
        self.assertTrue(recent[0].endswith(f"p{settings.RECENT_MAX + 2}.jfgproj"))

    def test_gui_opens_last_else_fallback(self):
        calls = []
        with mock.patch("jfgstudio.gui.app.run", lambda **kw: calls.append(kw["project_path"]) or 0):
            cli.main(["gui", "--fallback", "sample.jfgproj"])
            last = self.project("mine")
            settings.note_recent(last)
            cli.main(["gui", "--fallback", "sample.jfgproj"])
            cli.main(["gui", "other.jfgproj"])
        self.assertEqual(calls, ["sample.jfgproj", last, "other.jfgproj"])


if __name__ == "__main__":
    unittest.main()
