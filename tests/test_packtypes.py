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

    def test_example(self):
        self.assertEqual(packtypes.example(TYPES, 0xB9, None), [406, 31])  # its base's own
        self.assertEqual(packtypes.example(TYPES, 0x283, 49), [5, 6])  # the most placed type of its behaviour
        self.assertIsNone(packtypes.example(TYPES, 0x283, 99))

    def test_validate(self):
        hub = proj.load(self.hub.path)
        level = proj.new_level(hub, "room", "Room", "stock:track/1")
        level.objects["objects"].append({"uid": "x", "type": "someone_library:objtype/missing", "pos": [0, 0, 0],
                                         "size": packtypes.PLAIN_ENTRY})
        self.assertIn(("type", val.ERROR), {(p.rule, p.severity) for p in val.validate(hub)})


if __name__ == "__main__":
    unittest.main()
