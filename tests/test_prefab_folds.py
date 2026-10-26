"""Where the Prefabs window lists a type: its category and the fold of its kind (made-up types, no ROM)."""

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


if __name__ == "__main__":
    unittest.main()
