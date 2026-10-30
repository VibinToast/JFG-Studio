"""What the owner's play-tests of an agent-built beach level found, as checks and fixes (synthetic geometry only)."""

import json
import os
import tempfile
import unittest
from pathlib import Path

import numpy as np

from jfgstudio import agent, geometry_checks, layout_study, prefabs
from jfgstudio import objects as obj
from jfgstudio import project as proj
from jfgstudio import validate as val
from jfgstudio.gui.viewport import heights_at
from tests.fixtures import sample_project
from tests.test_agent import KILL, QuadMesh, _block, _quad, fake_mesh


def _grid(n: int, cell: float, y: float = 0.0) -> list:
    out = []
    for i in range(n):
        for j in range(n):
            x, z = -n * cell / 2 + i * cell, -n * cell / 2 + j * cell
            out += _quad(x, z, x + cell, z + cell, y)
    return out


class FacingMesh(QuadMesh):
    """QuadMesh whose floors face up, as TrackMesh's do (a ceiling is not a floor)."""

    def floor_height(self, x, z, below=None):
        ys = heights_at(self.collision, x, z, facing=1)
        if below is not None:
            ys = ys[ys <= below + 1.0]
        return float(ys.max()) if len(ys) else None


def _down(quads) -> list:
    """The same faces turned to face down (a ceiling)."""
    return [[t[0], t[2], t[1]] for t in quads]


class Geometry(unittest.TestCase):
    def test_slivers_across_a_segment_split(self):
        ground = _grid(30, 100)  # 1800 triangles: the importer splits them into segments
        self.assertEqual(geometry_checks.slivers(np.array(ground, "f8")), [])
        sliver = [[(-1400, 1, -40), (1400, 1, -40), (1400, 1, -60)]]  # 2800 long, over every split along x
        found = geometry_checks.slivers(np.array(ground + sliver, "f8"))
        self.assertEqual(len(found), 1)

    def test_coplanar_overlaps(self):
        a = _quad(0, 0, 100, 100, 0)
        self.assertEqual(geometry_checks.coplanar(np.array(a + _quad(100, 0, 200, 100, 0), "f8")), [])  # side by side
        self.assertTrue(geometry_checks.coplanar(np.array(a + _quad(50, 50, 150, 150, 0), "f8")))  # overlapping
        self.assertTrue(geometry_checks.coplanar(np.array(a + _down(a), "f8")))  # a decal's back in its front's place
        self.assertEqual(geometry_checks.coplanar(np.array(a + _quad(50, 50, 150, 150, 2), "f8")), [])  # 2 apart

    def test_water_under_floor(self):
        water = np.array(_quad(-100, -100, 100, 100, -8), "f8")
        sand = np.array(_quad(-500, -500, 500, 500, 0), "f8")
        self.assertTrue(geometry_checks.water_under_floor(sand, water))
        deep = np.array(_quad(-100, -100, 100, 100, -40), "f8")  # a deck 40 over water is walked on
        self.assertEqual(geometry_checks.water_under_floor(sand, deep), [])


class Cover(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = sample_project(Path(self.tmp.name))
        self.level = self.p.levels["gemini"]
        self.schemas = obj.load_schemas()

    def tearDown(self):
        self.tmp.cleanup()

    def test_region_ground_and_thin_obstacles(self):
        boulder = _block(-60, -60, 60, 60, 0, 120)  # the squadron stands on it
        crate = _block(150, 150, 260, 260, 0, 100)
        rack = _block(-250, 150, -70, 170, 0, 100)  # 180 x 20: no cover
        mesh = QuadMesh(_quad(-1000, -1000, 1000, 1000, 0) + boulder + crate + rack, (), KILL)
        poly = [(-300, -300), (300, -300), (300, 300), (-300, 300)]
        self.assertEqual(prefabs.region_ground(mesh, poly), 0)
        placer = prefabs.Placer(self.level, self.schemas)
        region = placer.region((0, 0, 0))
        squadron = placer.single(prefabs.SQUADRON_ID, (0, 120, 0), (1, 1), fields={"region": region})
        got = placer.cover(mesh, squadron)
        self.assertEqual(got["holes"], 2)  # the boulder and the crate; the rack is too thin
        self.assertEqual(got["small"], 1)

    def test_crates_in_a_squad_region(self):
        crate = {"uid": "crate1", "type": "stock:objtype/0x135", "pos": [0, 0, 0], "size": 0x10, "fields": {}}
        self.level.objects["objects"].append(crate)  # inside the sample squad's region 1
        rules = [p for p in val.validate(self.p) if p.rule == "squad-clutter"]
        self.assertEqual([p.uid for p in rules], ["crate1"])
        crate["pos"] = [1500, 0, 0]
        self.assertFalse([p for p in val.validate(self.p) if p.rule == "squad-clutter"])


class AgentFixes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = os.environ.get("JFGSTUDIO_CONFIG_DIR")
        os.environ["JFGSTUDIO_CONFIG_DIR"] = str(Path(self.tmp.name) / "config")
        sample_project(Path(self.tmp.name))
        self.s = agent.Session()
        self.s._catalog_error = "tests have no catalog"
        self.s.open(Path(self.tmp.name) / "sample.jfgproj")
        self.mesh = fake_mesh()
        self.s._mesh = lambda name: self.mesh

    def tearDown(self):
        if self.old is None:
            del os.environ["JFGSTUDIO_CONFIG_DIR"]
        else:
            os.environ["JFGSTUDIO_CONFIG_DIR"] = self.old
        self.tmp.cleanup()

    def test_x_z_lands_under_a_canopy(self):
        fronds = _quad(-150, -150, 150, 150, 260)  # one-sided, open at the sides
        self.mesh = QuadMesh(_quad(-1000, -1000, 1000, 1000, 0) + fronds, (), KILL)
        o = self.s.place("home", "health", [0, 0])[0]
        self.assertLess(o["pos"][1], 20)  # the ground, not the fronds' top
        hut = _block(-150, -150, 150, 150, 0, 200)  # a closed hut: its ground is inside it
        self.mesh = QuadMesh(_quad(-1000, -1000, 1000, 1000, 0) + hut, (), KILL)
        self.assertGreater(self.s.place("home", "health", [0, 0])[0]["pos"][1], 199)

    def test_no_clipping_or_burying_under_a_roof(self):
        roof = _down(_quad(-400, -400, 400, 400, 180))  # a hold's ceiling over a key
        cabin = (_down(_quad(500, -100, 700, 100, 150)) + _block(480, -120, 500, 120, 0, 150)
                 + _block(700, -120, 720, 120, 0, 150) + _block(480, -120, 720, -100, 0, 150)
                 + _block(480, 100, 720, 120, 0, 150))
        self.mesh = FacingMesh(_quad(-1000, -1000, 1000, 1000, 0) + roof + cabin, (), KILL)
        key = self.s.place("home", "key", [0, 0])[0]["uid"]
        crate = self.s.place("home", "health", [600, 0])[0]["uid"]
        found = {f["uid"]: f["problem"] for f in self.s.check_placement("home")}
        self.assertNotIn(key, found)
        self.assertNotIn(crate, found)

    def test_set_project_keeps_hand_edits(self):
        path = self.s.p.path / "project.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["description"] = "written by hand"
        path.write_text(json.dumps(doc), encoding="utf-8")
        self.s.p.save()  # nothing in project.json changed in the session: the hand edit stays
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["description"], "written by hand")
        out = self.s.set_project({"tags": ["Levels"], "shortDescription": "A beach"})
        self.assertEqual(out["description"], "written by hand")
        self.assertEqual(proj.load(self.s.p.path).meta["tags"], ["Levels"])
        with self.assertRaises(agent.AgentError):
            self.s.set_project({"id": "other"})


class Layout(unittest.TestCase):
    def test_backdrop_left_out(self):
        land = _grid(10, 100)  # 1000 x 1000 round the start
        far = _quad(4000, 4000, 4600, 4600, 0)  # distant piers
        stats, _ = layout_study.study(np.array(land + far, "f8"), [(0.0, 0.0)])
        self.assertLess(stats["footprint"][0], 1200)
        self.assertGreater(stats["backdrop"], 0)
        whole, _ = layout_study.study(np.array(land + far, "f8"))
        self.assertGreater(whole["footprint"][0], 4000)
        self.assertNotIn("backdrop", whole)


if __name__ == "__main__":
    unittest.main()
