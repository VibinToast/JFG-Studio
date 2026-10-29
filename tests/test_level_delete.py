"""Deleting a level (project.delete_level) and what still names it (project.level_references)."""
import json
import tempfile
import unittest
from pathlib import Path

from jfgstudio import project as proj


class LevelDeleteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.p = proj.create(Path(self.tmp.name) / "pack.jfgproj", "me_pack", "Pack")
        proj.new_level(self.p, "room", "Room", "stock:track/14")
        proj.new_level(self.p, "hall", "Hall", "stock:track/14")
        proj.edit_stock_level(self.p, "stock16", 16, "Cave", 14)
        self.p.save()

    def test_references(self):
        hall = Path(self.p.path) / "levels" / "hall" / "objects.json"
        doc = json.loads(hall.read_text())
        doc["exitTargets"] = ["pack:level/room", "pack:level/roomy"]  # names it (and a longer name that doesn't)
        hall.write_text(json.dumps(doc))
        self.assertEqual(proj.level_references(self.p, "room"), ["levels/hall/objects.json"])
        self.assertEqual(proj.level_references(self.p, "stock16"), [])

    def test_delete(self):
        proj.delete_level(self.p, "stock16")
        self.assertNotIn("stock16", self.p.levels)
        self.assertFalse((Path(self.p.path) / "levels" / "stock16").exists())
        again = proj.load(self.p.path)
        self.assertEqual(sorted(again.levels), ["hall", "room"])
        with self.assertRaises(proj.ProjectError):
            proj.delete_level(self.p, "stock16")


if __name__ == "__main__":
    unittest.main()
