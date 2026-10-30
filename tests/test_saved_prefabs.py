import tempfile
import unittest
from pathlib import Path

from jfgstudio import edit, prefabs
from jfgstudio import objects as obj
from jfgstudio import project as proj
from jfgstudio import validate as val
from tests.fixtures import sample_project


class SavedPrefabs(unittest.TestCase):
    """Objects saved together (prefabs/<name>.json) and placed again with fresh uids and numbers."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = sample_project(Path(self.tmp.name))
        self.level = self.p.levels["gemini"]
        self.schemas = obj.load_schemas()
        squad = {"squad1", "ant1", "ant2", "door1", "rp0", "rp1", "rp2", "rp3"}
        self.objs = [o for o in self.level.objects["objects"] if o["uid"] in squad]

    def tearDown(self):
        self.tmp.cleanup()

    def capture(self, objs, mesh=None):
        return prefabs.capture(objs, "Ant ambush", self.schemas, values=lambda o, s: dict(o.get("fields", {})),
                               mesh=mesh, description=" a squad and its door ")

    def place(self, doc, pos, mesh=None):
        placer = prefabs.Placer(self.level, self.schemas)
        made = placer.saved(doc, pos, mesh=mesh)
        edit.History(self.level).add(made)
        return made

    def test_capture_is_relative_to_its_anchor(self):
        doc = self.capture(self.objs)
        self.assertEqual(doc["description"], "a squad and its door")
        self.assertEqual(len(doc["objects"]), 8)
        xs = [o["pos"][0] for o in doc["objects"]]
        self.assertEqual(min(xs) + max(xs), 0)  # the footprint's middle
        self.assertEqual(min(o["pos"][1] for o in doc["objects"]), 0)  # the lowest one's height

    def test_placed_again_with_its_own_numbers(self):
        made = self.place(self.capture(self.objs), (2000, 0, 2000))
        squad = next(o for o in made if o["type"] == "stock:objtype/0x4F")
        self.assertNotEqual(squad["fields"]["squadronId"], 7)
        self.assertNotEqual(squad["fields"]["doorId"], 1)
        self.assertNotEqual(squad["fields"]["region"], 1)
        members = [o for o in made if o.get("schema") == "squadmember"]
        self.assertEqual({m["fields"]["squadron"] for m in members}, {squad["fields"]["squadronId"]})
        door = next(o for o in made if o.get("schema") == "door")
        self.assertEqual(door["fields"]["doorId"], squad["fields"]["doorId"])
        corners = [o for o in made if o["type"] == "stock:objtype/0x5"]
        self.assertEqual({c["fields"]["region"] for c in corners}, {squad["fields"]["region"]})
        uids = [o["uid"] for o in self.level.objects["objects"]]
        self.assertEqual(len(uids), len(set(uids)))
        rules = {p.rule for p in val.validate(self.p, self.schemas) if p.severity == val.ERROR}
        self.assertEqual(rules, set())

    def test_a_number_it_only_names_is_kept(self):
        made = self.place(self.capture([o for o in self.objs if o["uid"] in ("squad1", "ant1")]), (0, 0, 0))
        squad = next(o for o in made if o["type"] == "stock:objtype/0x4F")
        self.assertNotEqual(squad["fields"]["squadronId"], 7)  # it gives that out
        self.assertEqual(squad["fields"]["doorId"], 1)  # the level's door, not in the prefab
        self.assertEqual(squad["fields"]["region"], 1)

    def test_start_points_get_free_numbers(self):
        start = next(o for o in self.level.objects["objects"] if o["uid"] == "start0")
        made = self.place(self.capture([start, dict(start, uid="start1")]), (0, 0, 300))
        self.assertEqual(sorted(o["fields"]["startPoint"] for o in made), [1, 2])

    def test_lift_keeps_the_height_over_the_floor(self):
        from tests.test_agent import KILL, QuadMesh, _quad
        low = QuadMesh(_quad(-3000, -3000, 3000, 3000, 0), (), KILL)
        high = QuadMesh(_quad(-3000, -3000, 3000, 3000, 200), (), KILL)
        start = next(o for o in self.level.objects["objects"] if o["uid"] == "start0")
        doc = self.capture([start], mesh=low)
        self.assertEqual(doc["objects"][0]["lift"], 10)
        made = self.place(doc, (500, 195, 500), mesh=high)  # dropped where a ray meets the floor, near enough
        self.assertEqual(made[0]["pos"], [500, 210, 500])
        self.assertNotIn("lift", made[0])

    def test_saved_and_loaded(self):
        name = proj.save_prefab(self.p, self.capture(self.objs))
        self.assertEqual(name, "ant_ambush")
        again = proj.load(self.p.path)
        self.assertEqual(again.prefabs["ant_ambush"]["name"], "Ant ambush")
        proj.delete_prefab(again, name)
        self.assertEqual(proj.load(self.p.path).prefabs, {})
        with self.assertRaises(proj.ProjectError):
            proj.save_prefab(self.p, {"format": 1, "name": "Empty", "objects": []})

    def test_game_objects_become_copies(self):
        game = {"uid": "stock:3:12", "name": "Crate", "type": "stock:objtype/0x29", "pos": [5, 0, 5],
                "fields": {}, "stock": {"list": 3, "entry": 12}, "game": {"list": 3, "index": 12}}
        o = self.capture([game])["objects"][0]
        self.assertEqual(o["uid"], "Crate")
        self.assertNotIn("game", o)
        self.assertEqual(o["stock"], {"list": 3, "entry": 12})


class PreviewPaths(unittest.TestCase):
    def test_paths_drawn(self):
        import struct
        import zlib

        from jfgstudio import preview
        from tests.test_agent import KILL, QuadMesh, _quad
        mesh = QuadMesh(_quad(-1000, -1000, 1000, 1000, 0), (), KILL)
        path = {"object": "stock:objtype/0x47", "points": [[-500, 60, 0], [500, 60, 0], [0, 60, 500]], "loop": True}

        def has_path(png):
            w, h = struct.unpack(">II", png[16:24])
            raw = zlib.decompress(png[png.index(b"IDAT") + 4:png.index(b"IEND") - 8])
            return any(bytes(preview.PATH_RGB) in raw[y * (3 * w + 1) + 1:(y + 1) * (3 * w + 1)] for y in range(h))
        png, legend = preview.render(mesh, [], obj.load_schemas(), size=128, paths=[path])
        self.assertIn("path", legend["marks"])
        self.assertTrue(has_path(png))
        self.assertFalse(has_path(preview.render(mesh, [], obj.load_schemas(), size=128)[0]))


if __name__ == "__main__":
    unittest.main()
