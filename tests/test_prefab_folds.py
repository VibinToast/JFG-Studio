"""Where the Prefabs window lists a type (its category and the fold of its kind), and where an exit leads (made-up
data, no ROM)."""

import unittest

from jfgstudio import objects as obj
from jfgstudio.gui import app


class Folds(unittest.TestCase):
    def test_a_decoded_type_by_its_schema(self):
        schema = obj.load_schemas()["key"]
        self.assertEqual(app._prefab_place({"behaviour": 60}, schema), (schema.category, app._plural(schema.name)))

    def test_an_undecoded_type_by_its_behaviour(self):
        self.assertEqual(app._prefab_place({"behaviour": 43}, None), ("Pickups", "Weapon pickups"))
        self.assertEqual(app._prefab_place({"behaviour": 9999}, None), ("Other (fields not decoded)", None))

    def test_plurals(self):
        self.assertEqual(app._plural("Door lock light"), "Door lock lights")
        self.assertEqual(app._plural("Exit (loading zone)"), "Exits (loading zone)")
        self.assertEqual(app._plural("Ammo"), "Ammo")
        self.assertEqual(app._plural("Breakable glass"), "Breakable glass")


class ExitTarget(unittest.TestCase):
    def test_the_level_its_bytes_name(self):
        from jfgstudio.gui import level_tools
        game = {"uid": "stock:1:2", "stock": {"list": 1, "entry": 2}}
        self.assertEqual(level_tools.exit_target(game, {"levelLow": 26, "levelHigh": 255}), "stock:level/26")
        self.assertEqual(level_tools.exit_target(game, {"levelLow": 4, "levelHigh": 1}), "stock:level/260")
        self.assertEqual(level_tools.exit_target({**game, "target": "pack:level/x"}, {}), "pack:level/x")
        self.assertEqual(level_tools.exit_target({"uid": "exit1"}, {"levelLow": 0}), "")  # a new one: unset


if __name__ == "__main__":
    unittest.main()
