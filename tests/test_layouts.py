"""Saved window layouts (gui/layouts.py): names, files, and a load waiting for the next frame."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jfgstudio.gui import layouts


class LayoutTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "layouts"
        patcher = mock.patch.object(layouts, "folder", return_value=self.dir)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)
        layouts._pending = None

    def test_clean_name(self):
        self.assertEqual(layouts.clean_name("  My layout: big scene!  "), "My layout big scene")
        self.assertEqual(layouts.clean_name("../../evil"), "evil")
        self.assertEqual(layouts.clean_name("x" * 60), "x" * 40)

    def test_save_list_load_delete(self):
        with mock.patch.object(layouts.imgui, "save_ini_settings_to_memory", return_value="[Window][Scene]\n"):
            self.assertEqual(layouts.save("Big scene"), "Big scene")
        with self.assertRaises(ValueError):
            layouts.save("!!!")
        self.assertEqual(layouts.names(), ["Big scene"])
        self.assertFalse(layouts.load("missing"))
        self.assertTrue(layouts.load("Big scene"))
        self.assertEqual(layouts._pending, "[Window][Scene]\n")
        with mock.patch.object(layouts.imgui, "load_ini_settings_from_memory") as load:
            layouts.pre_new_frame()
            load.assert_called_once_with("[Window][Scene]\n")
            layouts.pre_new_frame()  # nothing waiting: no second load
            load.assert_called_once()
        layouts.delete("Big scene")
        self.assertEqual(layouts.names(), [])


if __name__ == "__main__":
    unittest.main()
