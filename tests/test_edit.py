import tempfile
import unittest
from pathlib import Path

import numpy as np

from jfgstudio import edit
from jfgstudio import project as proj
from tests.fixtures import sample_project


class History(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = sample_project(Path(self.tmp.name))
        self.level = self.p.levels["gemini"]
        self.h = edit.History(self.level)

    def tearDown(self):
        self.tmp.cleanup()

    def snapshot(self):
        return proj.dumps(self.level.objects)

    def test_set_undo_redo(self):
        before = self.snapshot()
        self.h.set_value("door1", ("fields", "doorId"), 3)
        self.assertEqual(edit.find(self.level, "door1")["fields"]["doorId"], 3)
        self.assertEqual(self.h.undo(), "set fields.doorId")
        self.assertEqual(self.snapshot(), before)
        self.h.redo()
        self.assertEqual(edit.find(self.level, "door1")["fields"]["doorId"], 3)
        self.assertTrue(self.h.dirty)

    def test_set_same_value_is_not_a_step(self):
        self.h.set_value("door1", ("fields", "doorId"), 1)
        self.assertEqual(self.h.done, [])

    def test_move_is_one_step(self):
        o = edit.find(self.level, "ant1")
        start = list(o["pos"])
        o["pos"] = [1, 2, 3]  # the gizmo moved it live
        o["pos"] = [10, 20, 30]
        self.h.move({"ant1": (start, list(o["pos"]))})
        self.assertEqual(len(self.h.done), 1)
        self.h.undo()
        self.assertEqual(o["pos"], start)

    def test_delete_and_duplicate(self):
        before = self.snapshot()
        uids = self.h.duplicate({"ant1", "ant2"})
        self.assertEqual(len(uids), 2)
        self.assertEqual(len(set(uids) | {o["uid"] for o in self.level.objects["objects"]}),
                         len(self.level.objects["objects"]))
        self.h.delete({"ant1", "door1"})
        self.assertIsNone(edit.find(self.level, "door1"))
        self.h.undo()
        self.h.undo()
        self.assertEqual(self.snapshot(), before)  # order kept, so saved-flag order is kept too

    def test_new_uid(self):
        self.assertEqual(edit.new_uid(self.level, "ant"), "ant3")
        self.assertEqual(edit.new_uid(self.level, "Big Door"), "big_door1")

    def test_redo_cleared_by_new_edit(self):
        self.h.set_value("door1", ("fields", "doorId"), 3)
        self.h.undo()
        self.h.set_value("door1", ("fields", "doorId"), 4)
        self.assertIsNone(self.h.redo())


class Floor(unittest.TestCase):
    def test_floor_height(self):
        from jfgstudio.gui.viewport import TrackMesh
        m = TrackMesh.__new__(TrackMesh)
        # Two floors over the same square: the ground at 0 and a platform at 50 over half of it.
        # Wound to face up, as floors are: a ceiling (facing down) is never a floor.
        ground = [[(-100, 0, -100), (100, 0, 100), (100, 0, -100)], [(-100, 0, -100), (-100, 0, 100), (100, 0, 100)]]
        platform = [[(0, 50, -100), (100, 50, 100), (100, 50, -100)]]
        roof = [[(-100, 300, -100), (100, 300, -100), (100, 300, 100)]]  # facing down
        m.collision = np.array(ground + platform + roof, float)
        self.assertEqual(m.floor_height(50, -50), 50)          # the highest floor
        self.assertEqual(m.floor_height(50, -50, below=20), 0)  # the one under an object that is below the platform
        self.assertEqual(m.floor_height(-50, 50), 0)
        self.assertIsNone(m.floor_height(500, 0))


if __name__ == "__main__":
    unittest.main()
