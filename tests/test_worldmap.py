"""The planet registry (worldmap.py) on a small synthetic map."""

import tempfile
import unittest
from pathlib import Path

from jfgstudio import worldmap
from tests.fixtures import sample_project

# Two discs and a station: 0 at (100, 100), 1 below it, station 2 to the right of 0.
DOC = {
    "icons": [{"x": 100, "y": 100, "radius": 10}, {"x": 100, "y": 300, "radius": 12}],
    "points": [{"kind": "disc", "index": 0, "world": 3}, {"kind": "disc", "index": 1, "world": 4},
               {"kind": "object", "index": 9, "world": 5}],
    "routes": [{"from": 0, "to": 1, "flags": [3, 2], "corners": [[100, 123], [100, 275]]},
               {"from": 0, "to": 2, "flags": [1, 0], "corners": [[123, 100], [200, 100]]}],
}


class WorldMap(unittest.TestCase):
    def test_directions_and_lines(self):
        self.assertEqual(worldmap.taken_directions(DOC, 0), {"down", "right"})
        self.assertEqual(worldmap.taken_directions(DOC, 1), {"up"})
        self.assertEqual(worldmap.point_positions(DOC)[2], (200.0, 100.0))
        # straight up from point 0 to a disc above it
        self.assertEqual(worldmap.auto_corners(DOC, 0, "up", {"x": 100, "y": 20, "radius": 8}), [[100, 77], [100, 41]])
        # left, then down to a disc lower and further left
        self.assertEqual(worldmap.auto_corners(DOC, 0, "left", {"x": 30, "y": 180, "radius": 6}),
                         [[77, 100], [30, 100], [30, 161]])

    def test_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = sample_project(Path(tmp))
            p.planets.clear()
            p.planets["mine"] = {"format": 1, "displayName": {"en": "Mine"},
                                 "disc": {"index": 1, "x": 100, "y": 20, "radius": 8, "colour": [1, 2, 3]},
                                 "routes": [{"from": "stock:mappoint/0", "dir": "up", "back": "down"}],
                                 "entryLevel": {"vela": "pack:level/home", "juno": "pack:level/home",
                                                "lupus": "stock:level/21"}}
            world, problems = worldmap.manifest(p, DOC)
            self.assertEqual(problems, [])
            planet = world["planets"][0]
            self.assertEqual(planet["levels"], ["level_home", "level_home", "stock:21"])
            self.assertEqual(planet["disc"], {"looks": 1, "x": 100, "y": 20, "radius": 8, "colour": [1, 2, 3]})
            self.assertEqual(planet["routes"], [{"from": 0, "dir": 2, "back": 3, "corners": [[100, 77], [100, 41]]}])
            p.planets["mine"]["routes"][0]["dir"] = "down"
            _, problems = worldmap.manifest(p, DOC)
            self.assertIn("free directions there: left, up", problems[0])
            _, problems = worldmap.manifest(p, None)
            self.assertIn("needs the catalog", problems[0])


class OnGround(unittest.TestCase):
    def test_lift(self):
        from jfgstudio import objects as obj
        schemas = obj.load_schemas()
        self.assertEqual(obj.on_ground(-2.6, schemas["squadmember"]), 6)  # rounded up, then lifted: never inside a slope
        self.assertEqual(obj.on_ground(3.2, schemas["health"]), 6)
        self.assertEqual(obj.on_ground(0.0, None), 8)
