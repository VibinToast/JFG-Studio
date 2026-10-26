import math
import struct
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


class Cover(unittest.TestCase):
    """Squads get cover in the editor as for agents: obstacles in a squad's region, and holes drawn by hand."""

    def setUp(self):
        from tests.test_agent import KILL, QuadMesh, _block, _quad
        self.tmp = tempfile.TemporaryDirectory()
        self.p = sample_project(Path(self.tmp.name))
        self.level = self.p.levels["gemini"]
        self.schemas = obj.load_schemas()
        self.crate = _block(-80, -60, 0, 20, 0, 90)  # inside a squad's square around (0, 0, 0)
        self.mesh = QuadMesh(_quad(-1000, -1000, 1000, 1000, 0) + self.crate, (), KILL)
        self.flat = QuadMesh(_quad(-1000, -1000, 1000, 1000, 0), (), KILL)

    def tearDown(self):
        self.tmp.cleanup()

    def placer(self):
        return prefabs.Placer(self.level, self.schemas, {0x1AD: 7, 0x78: 24})

    def keep(self, placer):
        edit.History(self.level).add(placer.made)
        return placer.made

    def squad(self, mesh):
        placer = self.placer()
        placer.squad((0, 0, 0), (424, 260), 0x78, (424, 261), members=2, mesh=mesh)
        return self.keep(placer)

    def test_squad_group_gets_holes(self):
        made = self.squad(self.mesh)
        squad = next(o for o in made if o["type"] == "stock:objtype/0x4F")
        info = [o for o in made if o["type"] == "stock:objtype/0x69"]
        self.assertEqual(len(info), 1)
        self.assertEqual((info[0]["fields"]["flags"], info[0]["fields"]["link"]),
                         (prefabs.HOLE, squad["fields"]["region"]))
        hole = self.placer().corners(info[0]["fields"]["region"])
        pts = [(o["pos"][0], o["pos"][2]) for o in hole]
        self.assertLess(prefabs.winding(pts), 0)  # clockwise
        xs, zs = [p[0] for p in pts], [p[1] for p in pts]
        self.assertTrue(min(xs) <= -80 and max(xs) >= 0 and min(zs) <= -60 and max(zs) >= 20)  # round the crate
        self.assertEqual(self.placer().holes_of(squad["fields"]["region"]), [info[0]["fields"]["region"]])
        rules = {p.rule for p in val.validate(self.p, self.schemas) if p.severity == val.ERROR}
        self.assertEqual(rules, set())

    def test_no_mesh_no_cover_then_add_cover(self):
        made = self.squad(None)
        self.assertFalse([o for o in made if o["type"] == "stock:objtype/0x69"])
        squad = next(o for o in made if o["type"] == "stock:objtype/0x4F")
        flat = self.placer()
        got = flat.cover(self.flat, squad)  # open floor: nothing to hide behind
        self.assertEqual((got["holes"], got["old"], got["convex"]), (0, 0, True))
        self.assertFalse(flat.made)
        placer = self.placer()
        self.assertEqual(placer.cover(self.mesh, squad)["holes"], 1)  # the Inspector's Add cover
        self.keep(placer)
        again = self.placer()
        got = again.cover(self.mesh, squad)
        self.assertEqual((got["holes"], got["old"]), (0, 1))  # no second hole over the first
        self.assertFalse(again.made)
        with self.assertRaises(ValueError):
            self.placer().cover(self.mesh, {"pos": [0, 0, 0], "fields": {"region": 99}})

    def test_hole_drawn_by_hand(self):
        placer = self.placer()
        area = placer.polygon([(2000, 0, 2000), (2600, 0, 2000), (2600, 0, 2600), (2000, 0, 2600)])
        self.keep(placer)
        placer = self.placer()
        self.assertEqual(placer.area_at(2300, 2300), area)
        self.assertIsNone(placer.area_at(5000, 5000))
        drawn = [(2200, 0, 2200), (2400, 0, 2200), (2400, 0, 2400), (2200, 0, 2400)]  # counter-clockwise, as clicked
        n = placer.hole(drawn, area)
        self.keep(placer)
        later = self.placer()
        self.assertEqual(later.holes_of(area), [n])
        self.assertEqual(later.area_at(2300, 2300), area)  # a hole is never the area itself
        pts = [(o["pos"][0], o["pos"][2]) for o in later.corners(n)]
        self.assertLess(prefabs.winding(pts), 0)

    def test_holes_drawn_distinctly(self):
        import zlib

        from jfgstudio import preview
        from jfgstudio.gui import viewport as vp
        self.squad(self.mesh)
        objs = self.level.objects["objects"]
        behaviours = {0x1AD: 7, 0x78: 24}
        keys = {o["uid"]: (s.key if (s := obj.schema_for(o, self.schemas, behaviours)) else None) for o in objs}
        colours = [c for _, _, c in vp.region_links(objs, keys)]
        self.assertEqual(colours.count(vp.HOLE_COLOUR), 6)  # a box's 4 sides and its cross
        self.assertIn(vp.KIND_COLOURS["regionpoint"], colours)
        png, legend = preview.render(self.mesh, objs, self.schemas, behaviours, size=256)
        self.assertIn("hole", legend["marks"])
        w, h = struct.unpack(">II", png[16:24])
        raw = zlib.decompress(png[png.index(b"IDAT") + 4:png.index(b"IEND") - 8])
        rows = [raw[y * (3 * w + 1) + 1:(y + 1) * (3 * w + 1)] for y in range(h)]
        self.assertTrue(any(bytes(preview.HOLE_RGB) in r for r in rows))


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
