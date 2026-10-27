import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jfgstudio import build, settings, setup, testrun


class Setup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"JFGSTUDIO_CONFIG_DIR": self.tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_version(self):
        self.assertEqual(setup.version("1.2.3"), (1, 2, 3))
        self.assertEqual(setup.version("v1.3.0-dev"), (1, 3, 0))
        self.assertEqual(setup.version("1.3"), (1, 3, 0))
        self.assertLess(setup.version("1.2.3"), setup.version(build.DEFAULT_MIN_RECOMP))

    def test_needed_until_a_rom_is_set(self):
        with mock.patch.object(settings, "detected", return_value=None):
            self.assertTrue(setup.needed())
            settings.set_value("rom", "C:/my.z64")
            self.assertFalse(setup.needed())

    def test_rom(self):
        self.assertFalse(setup.check_rom("").ok)
        self.assertIn("no file", setup.check_rom(str(Path(self.tmp.name) / "none.z64")).text)
        junk = Path(self.tmp.name) / "junk.z64"
        junk.write_bytes(b"not a rom at all")
        self.assertFalse(setup.check_rom(str(junk)).ok)

    def test_game(self):
        empty = setup.check_game("")
        self.assertFalse(empty.ok)
        self.assertTrue(empty.warning)  # optional
        self.assertFalse(setup.check_game(self.tmp.name).ok)  # no exe there
        exe = Path(self.tmp.name) / testrun.EXE_NAMES[0]
        exe.write_bytes(b"")
        with mock.patch.object(testrun, "capabilities", return_value={"port_version": "1.2.3"}):
            old = setup.check_game(self.tmp.name)
        self.assertTrue(old.ok)
        self.assertTrue(old.warning)
        self.assertIn(build.DEFAULT_MIN_RECOMP, old.text)
        with mock.patch.object(testrun, "capabilities", return_value={"port_version": build.DEFAULT_MIN_RECOMP}):
            good = setup.check_game(self.tmp.name)
        self.assertTrue(good.ok)
        self.assertFalse(good.warning)


if __name__ == "__main__":
    unittest.main()
