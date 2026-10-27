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
            self.assertEqual(testrun.start_level_arg(p, "gemini", False), "412")
            self.assertEqual(testrun.start_level_arg(p, "gemini", True), "test_sample:level_gemini")
            del p.levels["gemini"].level["fixedId"]
            self.assertEqual(testrun.start_level_arg(p, "gemini", True), "test_sample:level_gemini")
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
        self.assertEqual(o["fields"]["yaw"], 48)  # facing +x: three quarters (yaw 0 faces -z in the game)
        self.assertEqual(o["size"], 0x0E)



def _png(w: int, h: int) -> bytes:
    import struct
    import zlib
    raw = b"".join(b"\0" + bytes(w * 4) for _ in range(h))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))  # noqa: E731
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


class Thumbnail(unittest.TestCase):
    def test_set_validate_remove(self):
        from jfgstudio import project as proj
        with tempfile.TemporaryDirectory() as tmp:
            p = sample_project(Path(tmp))
            self.assertIsNone(p.thumbnail_path())
            src = Path(tmp) / "Cover.PNG"
            src.write_bytes(_png(8, 4))
            self.assertEqual(proj.set_thumbnail(p, src).name, "thumb.png")
            self.assertEqual(proj.thumbnail_size(p.thumbnail_path().read_bytes()), (8, 4))
            self.assertEqual([x.rule for x in val._check_thumbnail(p)], ["thumbnail"])  # not square
            src.write_bytes(_png(8, 8))
            proj.set_thumbnail(p, src)
            self.assertFalse(val._check_thumbnail(p))
            dds = Path(tmp) / "cover.dds"
            dds.write_bytes(b"DDS " + (124).to_bytes(4, "little") + bytes(4) + (16).to_bytes(4, "little")
                            + (16).to_bytes(4, "little") + bytes(108))
            proj.set_thumbnail(p, dds)  # replaces the PNG: the runtime would take the DDS anyway
            self.assertEqual(sorted(f.name for f in p.path.glob("thumb.*")), ["thumb.dds"])
            self.assertFalse(val._check_thumbnail(p))
            (p.path / "thumb.dds").write_bytes(b"not a picture")
            self.assertEqual([x.rule for x in val._check_thumbnail(p)], ["thumbnail"])
            with self.assertRaises(proj.ProjectError):
                proj.set_thumbnail(p, Path(tmp) / "cover.jpg")
            self.assertIsNone(proj.set_thumbnail(p, None))
            self.assertIsNone(p.thumbnail_path())


class Music(unittest.TestCase):
    def test_music_manifest_and_validate(self):
        from jfgstudio import build
        with tempfile.TemporaryDirectory() as tmp:
            p = sample_project(Path(tmp))
            gem = p.levels["gemini"].level
            gem["music"] = "pack:music/theme"
            schemas = obj.load_schemas()
            rules = {x.rule for x in val.validate(p, schemas) if x.severity == val.ERROR}
            self.assertIn("music", rules)  # the file isn't there yet
            p.music_path("theme").parent.mkdir(exist_ok=True)
            p.music_path("theme").write_bytes(b"OggS" + bytes(60))
            self.assertFalse([x for x in val.validate(p, schemas) if x.severity == val.ERROR])
            self.assertEqual(build.music_manifest(p), {"tracks": {"theme": "music/theme.ogg"},
                                                       "levels": {"level_gemini": "theme"}})
            gem["fixedId"] = 83  # replaces a stock level: named by number
            self.assertEqual(build.music_manifest(p)["levels"], {"stock:83": "theme"})
            del gem["fixedId"]
            self.assertEqual(build.music_manifest(p)["levels"], {"level_gemini": "theme"})


class Kinds(unittest.TestCase):
    def test_kind_round_trip_and_schema(self):
        from jfgstudio import project as proj
        with tempfile.TemporaryDirectory() as tmp:
            p = sample_project(Path(tmp))
            p.kinds["fire_ant"] = {"format": 1, "name": "Fire Ant", "base": "stock:objtype/0x78", "scale": 1.2,
                                   "textures": {"0": "0.png"}}
            p.save()
            back = proj.load(p.path)
            self.assertEqual(back.kinds["fire_ant"]["scale"], 1.2)
            self.assertEqual(back.kind_texture("fire_ant", 3), p.path / "kinds" / "fire_ant" / "3.png")
            (p.path / "kinds" / "bad.json").write_text('{"format": 1, "name": "x", "base": "pack:kind/y"}')
            with self.assertRaises(proj.ProjectError):
                proj.load(p.path)


class Triggers(unittest.TestCase):
    def test_compile_and_check(self):
        from jfgstudio import scripts as scr
        with tempfile.TemporaryDirectory() as tmp:
            p = sample_project(Path(tmp))
            scr.save_triggers(p, [
                {"name": "hello", "level": "pack:level/home", "when": {"kind": "level start"},
                 "then": [{"do": "message", "text": 'say "hi"', "seconds": 2}]},
                {"name": "gate", "level": "stock:level/21", "when": {"kind": "enter", "box": [10, 20, -10, -20]},
                 "once": False, "then": [{"do": "unlock_door", "door": 3}, {"do": "music", "track": "nope"}]}])
            lua = scr.compile_triggers(scr.load_triggers(p), p)
            self.assertIn('jfg.level_id("level_home")', lua)
            self.assertIn('jfg.message("say \\"hi\\"", 2.0)', lua)
            self.assertIn("x >= -10.0 and x <= 10.0 and z >= -20.0 and z <= 20.0", lua)
            self.assertIn("jfg.unlock_door(3)", lua)
            problems = scr.check(p)
            self.assertEqual(len(problems), 1)
            self.assertIn("music/nope.ogg", problems[0])
            self.assertIn("scripts/_triggers.lua", scr.pack_scripts(p))

    def test_talker(self):
        from jfgstudio import scripts as scr
        with tempfile.TemporaryDirectory() as tmp:
            p = sample_project(Path(tmp))
            scr.save_triggers(p, [
                {"name": "scout", "level": "pack:level/home", "once": False,
                 "when": {"kind": "talk", "at": [100, -50], "radius": 120},
                 "then": [{"do": "lines", "lines": ["One", 'Two "2"'], "seconds": 3}]},
                {"name": "sign", "level": "pack:level/home", "when": {"kind": "talk", "at": [0, 0], "button": "Q"},
                 "then": []}])
            lua = scr.compile_triggers(scr.load_triggers(p), p)
            self.assertIn("(x - 100.0)^2 + (z - -50.0)^2 <= 14400.0", lua)
            self.assertIn("not near[1]", lua)
            self.assertIn('local t = {"One", "Two \\"2\\""}; line[1] = math.min((line[1] or 0) + 1, #t)', lua)
            self.assertIn('jfg.pressed("Q")', lua)
            problems = scr.check(p)
            self.assertEqual(len(problems), 1)
            self.assertIn("unknown button", problems[0])

    def test_weapons(self):
        from jfgstudio import scripts as scr
        with tempfile.TemporaryDirectory() as tmp:
            p = sample_project(Path(tmp))
            scr.save_weapons(p, {"0": {"delay": 8, "damage": 2}, "3": {}, "9": {"speed": 3000.0, "colour": 1}})
            self.assertEqual(set(scr.load_weapons(p)), {"0", "9"})
            lua = scr.pack_scripts(p)["scripts/_weapons.lua"].decode()
            self.assertIn("jfg.weapon(0, {damage = 2, delay = 8})", lua)
            problems = scr.check(p)
            self.assertEqual(len(problems), 2)
            scr.save_weapons(p, {})
            self.assertFalse(scr.weapons_path(p).exists())

    def test_weapon_slots(self):
        # the port takes weapons 0-14 only: weapon 15 must not get past the check
        from jfgstudio import scripts as scr
        self.assertEqual(scr.WEAPON_COUNT, 15)
        self.assertEqual(scr.WEAPONS[0][0], "Pistol")
        self.assertEqual(scr.WEAPONS[14][0], "Cluster Bombs")
        self.assertEqual(scr.WEAPONS[6][1], ("spread_x", "spread_y"))
        self.assertTrue(all(set(used) <= set(scr.WEAPON_FIELDS) for _, used in scr.WEAPONS))
        with tempfile.TemporaryDirectory() as tmp:
            p = sample_project(Path(tmp))
            scr.save_weapons(p, {"14": {"delay": 4}})
            self.assertEqual(scr.check(p), [])
            scr.save_weapons(p, {"14": {"delay": 4}, "15": {"delay": 4}})
            problems = scr.check(p)
            self.assertEqual(len(problems), 1)
            self.assertIn("weapon '15' is not 0-14", problems[0])

    def test_spawn(self):
        from jfgstudio import scripts as scr
        oid, tail = scr.spawn_entry({"do": "spawn", "type": "stock:objtype/0xA9", "fields": {"amount": 2}})
        self.assertEqual(oid, 0xA9)
        self.assertEqual(tail[0], 2)
        self.assertEqual(len(tail) % 2, 0)
        lua = scr._action({"do": "spawn", "type": "stock:objtype/0xA9", "pos": [0, 20, -60], "near_player": True},
                          None)
        self.assertIn("jfg.spawn(169, px + 0.0, py + 20.0, pz + -60.0, {", lua)
        with self.assertRaises(ValueError):
            scr.spawn_entry({"do": "spawn", "type": "stock:objtype/5"})
