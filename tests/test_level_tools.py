import tempfile
import unittest
from pathlib import Path

from jfgstudio import edit, formats, geometry, prefabs
from jfgstudio import objects as obj
from jfgstudio import validate as val
from tests.fixtures import sample_project


def _formats_or_skip(case):
    try:
        formats.load()
    except formats.FormatsUnavailable as e:
        case.skipTest(str(e))


class LevelEdits(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = sample_project(Path(self.tmp.name))
        self.level = self.p.levels["gemini"]
        self.h = edit.History(self.level)

    def tearDown(self):
        self.tmp.cleanup()

    def test_level_settings_undo_and_merge(self):
        hd = self.level.level["header"]
        before = hd["fogR"]
        for v in (10, 20, 30):  # a colour being dragged: one step
            self.h.set_value(edit.LEVEL, ("header", "fogR"), v, "level fogR", merge=True)
        self.assertEqual(len(self.h.done), 1)
        self.assertEqual(hd["fogR"], 30)
        self.h.done[-1].merge = False  # the drag ended
        self.h.set_value(edit.LEVEL, ("header", "fogR"), 40, "level fogR", merge=True)
        self.assertEqual(len(self.h.done), 2)
        self.h.undo()
        self.h.undo()
        self.assertEqual(hd["fogR"], before)
        self.h.set_value(edit.LEVEL, ("sky",), -1, "level sky")
        self.assertEqual(self.level.level["sky"], -1)

    def test_file_command_undo(self):
        path = Path(self.tmp.name) / "g.bin"
        path.write_bytes(b"old")
        self.h.write_file(path, b"new", "water plane")
        self.assertEqual(path.read_bytes(), b"new")
        self.h.undo()
        self.assertEqual(path.read_bytes(), b"old")
        self.h.redo()
        self.assertEqual(path.read_bytes(), b"new")

    def test_region_polygon(self):
        schemas = obj.load_schemas()
        placer = prefabs.Placer(self.level, schemas)
        n = placer.polygon([(0, 0, 0), (300, 0, 0), (300, 0, 300), (0, 0, 300), (-100, 0, 150)])
        self.assertNotEqual(n, 1)  # region 1 is the fixture's
        self.assertEqual([o["fields"]["corner"] for o in placer.made], [0, 1, 2, 3, 4])
        self.h.add(placer.made)
        self.assertFalse([p for p in val.validate(self.p, schemas) if p.severity == val.ERROR])


class Water(unittest.TestCase):
    def test_add_water_reuses_or_makes_a_material(self):
        _formats_or_skip(self)
        from jfgformats import gltf
        g = gltf.GlbWriter()
        g.doc.update({"meshes": [], "nodes": [], "scene": 0, "scenes": [{"nodes": []}]})
        data = geometry.add_water(g.to_bytes(), 0, 0, 200, 100, 12)
        doc = geometry.glb_json(data)
        self.assertEqual([m["name"] for m in doc["materials"]], ["water"])
        self.assertEqual(doc["materials"][0]["extras"]["batchFlags"] & 0x2000, 0x2000)
        again = geometry.glb_json(geometry.add_water(data, 300, 0, 400, 100, 12))
        self.assertEqual(len(again["materials"]), 1)  # the second plane reuses the material
        self.assertEqual(len(again["images"]), 1)
        self.assertEqual(again["scenes"][0]["nodes"], [0, 1])
        acc = again["accessors"][again["meshes"][1]["primitives"][0]["attributes"]["POSITION"]]
        self.assertEqual((acc["min"][1], acc["max"][1]), (12.0, 12.0))
        with self.assertRaises(geometry.GeometryError):
            geometry.add_water(data, 0, 0, 0, 100, 12)


class StartLevel(unittest.TestCase):
    def test_start_level_arg(self):
        from jfgstudio import testrun
        with tempfile.TemporaryDirectory() as tmp:
            p = sample_project(Path(tmp))
            self.assertEqual(testrun.start_level_arg(p, "gemini", True), "412")
            del p.levels["gemini"].level["fixedId"]
            self.assertEqual(testrun.start_level_arg(p, "gemini", True), "test.sample:level_gemini")
            with self.assertRaises(testrun.TestRunError):
                testrun.start_level_arg(p, "gemini", False)
            self.assertEqual(testrun.start_level_arg(p, "21", True), "21")


class Markers(unittest.TestCase):
    def test_markers_from_extras(self):
        _formats_or_skip(self)
        from jfgformats import gltf

        from jfgstudio import importer
        g = gltf.GlbWriter()
        g.doc.update({"scene": 0, "scenes": [{"nodes": [0, 1]}], "nodes": [
            {"name": "start", "translation": [10, 5, -20], "rotation": [0.5, 0.5, -0.5, 0.5],
             "extras": {"jfg_type": "stock:objtype/0xD", "jfg_fields": '{"startPoint": 2}'}},
            {"name": "plain", "translation": [1, 2, 3]}]})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "m.glb"
            g.write(str(path))
            found = importer.markers(path)
        self.assertEqual(len(found), 1)
        o = found[0]
        self.assertEqual(o["pos"], [10, 5, -20])
        self.assertEqual(o["fields"]["startPoint"], 2)
        self.assertEqual(o["fields"]["yaw"], 16)  # facing +x: a quarter turn
        self.assertEqual(o["size"], 0x0E)
