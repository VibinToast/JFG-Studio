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
        self.assertEqual(obj.on_ground(-2.6, schemas["squadmember"]), 6)  # rounded up, then lifted
        self.assertEqual(obj.on_ground(3.2, schemas["health"]), 6)
        self.assertEqual(obj.on_ground(0.0, None), 8)


class Galaxies(unittest.TestCase):
    def project(self, tmp):
        p = sample_project(Path(tmp))
        p.planets.clear()
        p.galaxies["far"] = {"format": 1, "name": "Far Away", "home": "one"}
        base = {"format": 1, "galaxy": "far",
                "entryLevel": {"vela": "pack:level/home", "juno": "pack:level/home", "lupus": "pack:level/home"}}
        p.planets["one"] = {**base, "displayName": {"en": "One"},
                            "disc": {"index": 0, "x": 100, "y": 100, "radius": 10},
                            "unlock": "start"}
        p.planets["two"] = {**base, "displayName": {"en": "Two"},
                            "disc": {"index": 1, "x": 220, "y": 100, "radius": 10},
                            "unlock": {"completed": "one"},
                            "routes": [{"from": "pack:world/one", "dir": "right", "back": "left"}]}
        return p

    def test_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self.project(tmp)
            world, problems = worldmap.manifest(p, None)  # a project galaxy needs no stock map
            self.assertEqual(problems, [])
            self.assertEqual(world["format"], 2)
            self.assertEqual(world["galaxies"], [{"id": "far", "name": "Far Away", "home": "one"}])
            two = next(q for q in world["planets"] if q["id"] == "two")
            self.assertEqual(two["galaxy"], "far")
            self.assertEqual(two["unlock"], {"completed": "one"})
            self.assertEqual(two["routes"], [{"from": "planet:one", "dir": 1, "back": 0,
                                              "corners": [[123, 100], [197, 100]]}])
            one = next(q for q in world["planets"] if q["id"] == "one")
            self.assertEqual((one["routes"], one["unlock"]), ([], "start"))

    def test_rules(self):
        from jfgstudio import validate as val
        with tempfile.TemporaryDirectory() as tmp:
            p = self.project(tmp)
            self.assertEqual([x.rule for x in val.validate(p) if x.severity == val.ERROR], [])
            p.planets["two"]["routes"] = []  # cut off from home
            p.planets["two"]["unlock"] = {"completed": "nowhere"}
            p.planets["one"]["routes"] = [{"from": "stock:mappoint/0", "dir": "up", "back": "down"}]
            rules = sorted(x.rule for x in val.validate(p) if x.severity == val.ERROR)
            self.assertEqual(rules, ["route", "route", "unlock"])

    def test_planet_regions(self):
        from jfgstudio import build
        from jfgstudio import validate as val
        with tempfile.TemporaryDirectory() as tmp:
            p = self.project(tmp)
            home = next(iter(p.levels))
            p.levels[home].level["planet"] = "two"
            b = object.__new__(build._Builder)  # regions() needs only the project
            b.p, b.log = p, lambda *_: None
            self.assertEqual(b.regions(), [{"append": "region_two", "name": "Two", "world": "Far Away"}])
            self.assertEqual(b.region_levels("two"), [home])
            self.assertEqual(b.region_levels("one"), [])
            self.assertEqual([x.rule for x in val.validate(p) if x.severity == val.ERROR], [])
            p.levels[home].level["planet"] = "nowhere"
            self.assertIn("planet", [x.rule for x in val.validate(p) if x.severity == val.ERROR])

    def test_trigger_actions(self):
        from jfgstudio import scripts as scr
        with tempfile.TemporaryDirectory() as tmp:
            p = self.project(tmp)
            scr.save_triggers(p, [{"name": "won", "level": "pack:level/home", "when": {"kind": "level start"},
                                   "then": [{"do": "complete planet", "planet": "one"},
                                            {"do": "unlock planet", "planet": "zzz"}]}])
            lua = scr.compile_triggers(scr.load_triggers(p), p)
            self.assertIn('jfg.complete_planet("one")', lua)
            self.assertEqual(len(scr.check(p)), 1)
