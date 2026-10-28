"""Object types of a pack's own and of a dependency (made-up projects, no ROM): the project files, resolving a type
to its base and the port's name for it, the stock entry it starts from, and the validator."""

import tempfile
import unittest

from jfgstudio import packtypes
from jfgstudio import project as proj
from jfgstudio import validate as val

TYPES = {0xB9: {"behaviour": 76, "examples": [[406, 31]], "uses": 52},
         0x283: {"behaviour": 46, "examples": []},
         0x30: {"behaviour": 49, "examples": [[1, 2]], "uses": 3}, 0x31: {"behaviour": 49, "examples": [[5, 6]],
                                                                         "uses": 9}}


class PackTypes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        self.lib = proj.create(f"{d}/lib.jfgproj", "someone_library", "Library")
        self.lib.objtypes["pad"] = {"name": "Pad", "base": "stock:objtype/0xB9", "model": 379}
        self.lib.save()
        self.hub = proj.create(f"{d}/hub.jfgproj", "someone_hub", "Hub")
        self.hub.meta["dependencies"] = [{"id": "someone_library", "version": "0.1.0", "project": "../lib.jfgproj"}]
        self.hub.objtypes["tank"] = {"name": "Tank", "base": "stock:objtype/0x283", "behaviour": 49}
        self.hub.save()

    def tearDown(self):
        self.tmp.cleanup()

    def test_round_trip(self):
        self.assertEqual(proj.load(self.lib.path).objtypes, self.lib.objtypes)

    def test_resolve(self):
        hub = proj.load(self.hub.path)
        own = packtypes.resolve(hub, "pack:objtype/tank")
        self.assertEqual((own["base"], own["behaviour"], own["fixup"]), (0x283, 49, "objtype_tank"))
        dep = packtypes.resolve(hub, "someone_library:objtype/pad")
        self.assertEqual((dep["base"], dep["model"], dep["fixup"]), (0xB9, 379, "someone_library:objtype_pad"))
        with self.assertRaises(packtypes.PackTypeError):
            packtypes.resolve(hub, "someone_library:objtype/nothing")
        with self.assertRaises(packtypes.PackTypeError):
            packtypes.resolve(hub, "someone_else:objtype/pad")  # not a dependency
        self.assertTrue(packtypes.is_pack_type("pack:objtype/tank"))
        self.assertFalse(packtypes.is_pack_type("stock:objtype/0xB9"))
        self.assertFalse(packtypes.is_pack_type("pack:kind/fire_ant"))

    def test_rotate(self):
        self.hub.objtypes["tank"].update({"copyModel": True, "rotate": 180})
        self.hub.save()
        self.assertEqual(packtypes.resolve(proj.load(self.hub.path), "pack:objtype/tank")["rotate"], 180)
        self.hub.objtypes["tank"]["rotate"] = 45  # quarter turns only
        self.hub.save()
        with self.assertRaises(proj.ProjectError):
            proj.load(self.hub.path)

    def test_example(self):
        self.assertEqual(packtypes.example(TYPES, 0xB9, None), [406, 31])  # its base's own
        self.assertEqual(packtypes.example(TYPES, 0x283, 49), [5, 6])  # the most placed type of its behaviour
        self.assertIsNone(packtypes.example(TYPES, 0x283, 99))

    def test_squad_of_a_dependency_type(self):
        # A squadron whose enemy is a dependency's type: the project loads, and the build names the type with its mod
        from jfgstudio import build
        from jfgstudio import objects as obj
        hub = proj.load(self.hub.path)
        level = proj.new_level(hub, "room", "Room", "stock:track/1")
        sq = {"uid": "sq", "type": "stock:objtype/0x4F", "schema": "squadron", "pos": [0, 0, 0], "size": 0x44,
              "fields": {"baddyType": 0xB9}, "kindFields": {"baddyType": "someone_library:objtype/pad"}}
        level.objects["objects"].append(sq)
        hub.save()
        hub = proj.load(self.hub.path)
        b = object.__new__(build._Builder)
        b.p, b.schemas = hub, obj.load_schemas()
        fixups = b.entry_fixups(sq, 0)
        offset = b.schemas["squadron"].field("baddyType").offset
        self.assertEqual(fixups, [{"at": offset, "type": "u16", "ref": "someone_library:objtype_pad"}])

    def test_mod_folder_dependency(self):
        # A hand-made mod (mod.toml and jfg_assets.json, no project): its objtypes placed by name, written as "like"
        import json
        from pathlib import Path

        from jfgstudio import agent, build
        from jfgstudio import objects as obj
        mod = Path(self.tmp.name) / "someone_beetle"
        mod.mkdir()
        (mod / "mod.toml").write_text('[manifest]\nid = "someone_beetle"\nversion = "0.2.0"\n', encoding="utf-8")
        (mod / "jfg_assets.json").write_text(json.dumps({"objtypes": [
            {"append": "objtype_beetle", "def": "objdef_beetle", "like": 0xB9},
            {"append": "objtype_spark", "def": "objdef_spark"}]}), encoding="utf-8")
        s = agent.Session(self.hub.path)
        with self.assertRaises(agent.AgentError):
            s.add_dependency("someone_other", None, mod_folder="../someone_beetle")  # not that mod's id
        out = s.add_dependency(None, None, mod_folder="../someone_beetle")
        self.assertEqual(out["dependencies"][-1], {"id": "someone_beetle", "version": "0.2.0",
                                                   "mod": "../someone_beetle"})
        self.assertEqual(set(out["types"]), {"someone_beetle:objtype/beetle", "someone_beetle:objtype/spark"})
        hub = proj.load(self.hub.path)
        info = packtypes.resolve(hub, "someone_beetle:objtype/beetle")
        self.assertEqual((info["base"], info["fixup"]), (0xB9, "someone_beetle:objtype_beetle"))
        with self.assertRaises(packtypes.PackTypeError):
            packtypes.resolve(hub, "someone_beetle:objtype/spark")  # no "like": Studio can't tell how to write it
        b = object.__new__(build._Builder)
        b.p, b.schemas = hub, obj.load_schemas()
        member = {"uid": "m", "type": "someone_beetle:objtype/beetle", "schema": "squadmember", "pos": [0, 0, 0],
                  "size": 0x20}
        self.assertEqual(b.entry_fixups(member, 8), [{"at": 8, "type": "u16", "ref": "someone_beetle:objtype_beetle"}])

    def test_validate(self):
        hub = proj.load(self.hub.path)
        level = proj.new_level(hub, "room", "Room", "stock:track/1")
        level.objects["objects"].append({"uid": "x", "type": "someone_library:objtype/missing", "pos": [0, 0, 0],
                                         "size": packtypes.PLAIN_ENTRY})
        self.assertIn(("type", val.ERROR), {(p.rule, p.severity) for p in val.validate(hub)})


if __name__ == "__main__":
    unittest.main()
