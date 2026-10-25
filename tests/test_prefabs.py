import math
import tempfile
import unittest
from pathlib import Path

import numpy as np

from jfgstudio import edit, prefabs
from jfgstudio import objects as obj
from jfgstudio import validate as val
from tests.fixtures import sample_project


class Placing(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = sample_project(Path(self.tmp.name))
        self.level = self.p.levels["gemini"]
        self.schemas = obj.load_schemas()

    def tearDown(self):
        self.tmp.cleanup()

    def place(self, fn, *args, **kw):
        placer = prefabs.Placer(self.level, self.schemas, {0x1AD: 7, 0x78: 24})
        getattr(placer, fn)(*args, **kw)
        edit.History(self.level).add(placer.made)
        return placer.made

    def test_single_from_defaults_and_from_stock(self):
        chest = self.place("single", 0x1AA, (10.4, 0, -3), name="Chest")[0]
        self.assertEqual(chest["size"], 0x14)
        self.assertEqual(chest["fields"]["size"], 100)
        self.assertNotIn("schema", chest)  # found by its id
        door = self.place("single", 0x1AD, (0, 0, 0), example=(424, 274))[0]
        self.assertEqual(door["schema"], "door")  # found by behaviour: named for the build
        self.assertEqual(door["stock"], {"list": 424, "entry": 274})
        with self.assertRaises(ValueError):  # partly decoded types need a stock entry
            prefabs.Placer(self.level, self.schemas, {0x1AD: 7}).single(0x1AD, (0, 0, 0))

    def test_squad_links_and_free_numbers(self):
        made = self.place("squad", (0, 0, 0), (424, 260), 0x78, (424, 261), members=3, door_oid=0x1AD,
                          door_example=(424, 274))
        keys = [obj.schema_for(o, self.schemas, {0x1AD: 7}).key for o in made]
        self.assertEqual(keys.count("regionpoint"), 4)
        self.assertEqual(keys.count("squadmember"), 3)
        squad = next(o for o in made if o["type"] == "stock:objtype/0x4F")
        door = next(o for o in made if o.get("schema") == "door")
        used_squads = {o["fields"]["squadronId"] for o in self.level.objects["objects"]
                       if o["type"] == "stock:objtype/0x4F" and o["uid"] != squad["uid"]}
        self.assertNotIn(squad["fields"]["squadronId"], used_squads)
        self.assertEqual(squad["fields"]["doorId"], door["fields"]["doorId"])
        self.assertEqual(squad["fields"]["count"], 3)
        corners = [o for o in made if o["type"] == "stock:objtype/0x5"]
        self.assertEqual({o["fields"]["region"] for o in corners}, {squad["fields"]["region"]})
        self.assertEqual(len({o["uid"] for o in self.level.objects["objects"]}), len(self.level.objects["objects"]))
        rules = {p.rule for p in val.validate(self.p, self.schemas) if p.severity == val.ERROR}
        self.assertEqual(rules, set())

    def test_exit_pair(self):
        made = self.place("exit_pair", (0, 0, 500))
        start = made[1]
        self.assertEqual(start["fields"]["startPoint"], 1)  # 0 is taken by the level's own start point
        edit.find(self.level, made[0]["uid"])["target"] = "pack:level/home"
        self.assertFalse([p for p in val.validate(self.p, self.schemas) if p.severity == val.ERROR])


class ViewMath(unittest.TestCase):
    def test_raycast(self):
        from jfgstudio.gui import viewport as vp
        tri = np.array([[[-10, 0, -10], [10, 0, -10], [0, 0, 10]]], "f4")
        self.assertAlmostEqual(vp.raycast(tri, (0, 50, 0), (0, -1, 0)), 50.0, places=4)
        self.assertIsNone(vp.raycast(tri, (0, 50, 0), (0, 1, 0)))
        self.assertIsNone(vp.raycast(tri, (100, 50, 0), (0, -1, 0)))

    def test_object_matrix_and_ray(self):
        from jfgstudio.gui import viewport as vp
        m = vp.object_matrix((5, 0, 0), yaw=math.pi / 2, scale=2.0)
        np.testing.assert_allclose(m @ [0, 0, 1, 1], [7, 0, 0, 1], atol=1e-9)  # +z forward turns to +x
        cam = vp.Camera()
        cam.yaw, cam.pitch = 0.0, 0.0
        origin, d = vp.screen_ray(cam, (200, 100), 100, 50)
        np.testing.assert_allclose(d, cam.forward(), atol=1e-6)
