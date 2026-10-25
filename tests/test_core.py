import os
import tempfile
import unittest
from pathlib import Path

from jfgstudio import cli, refs, settings
from jfgstudio import objects as obj
from jfgstudio import project as proj
from jfgstudio import validate as val
from tests.fixtures import clone, sample_project


class Refs(unittest.TestCase):
    def test_parse(self):
        r = refs.parse("stock:level/0x15", "level")
        self.assertEqual((r.namespace, r.category, r.number()), ("stock", "level", 21))
        self.assertTrue(refs.parse("pack:track/my_field").is_pack)
        self.assertEqual(str(refs.parse("antpack:kind/fire_ant")), "antpack:kind/fire_ant")

    def test_bad(self):
        for bad in ("level/21", "stock:level/abc", "pack:level/Bad Name", "pack:nonsense/x", 21):
            with self.assertRaises(refs.RefError, msg=bad):
                refs.parse(bad)
        with self.assertRaises(refs.RefError):
            refs.parse("pack:track/x", "level")


class Objects(unittest.TestCase):
    def setUp(self):
        self.schemas = obj.load_schemas()

    def test_schemas_load(self):
        self.assertTrue({"exit", "startpoint", "squadron", "squadmember", "door", "regionpoint", "chest", "light"}
                        <= set(self.schemas))
        ids = [i for s in self.schemas.values() for i in s.ids]
        self.assertEqual(len(ids), len(set(ids)), "an object id is in two schemas")
        for s in self.schemas.values():
            ends = sorted((f.offset, f.end, f.name) for f in s.fields)
            for a, b in zip(ends, ends[1:], strict=False):
                self.assertLessEqual(a[1], b[0], f"{s.key}: {a[2]} overlaps {b[2]}")
            for f in s.fields:
                self.assertGreaterEqual(f.offset, obj.PARAMS_OFFSET, f"{s.key}.{f.name}")
                self.assertLessEqual(f.end, s.min_size, f"{s.key}.{f.name}")
        self.assertEqual(self.schemas["squadron"].min_size, 0x44)
        self.assertEqual(self.schemas["door"].min_size, 0x24)
        self.assertEqual(self.schemas["exit"].min_size, 0x1C)

    def test_schema_lookup_and_angles(self):
        self.assertEqual(obj.schema_for_id(0x1AA, self.schemas).key, "chest")
        self.assertIsNone(obj.schema_for_id(0x999, self.schemas))
        self.assertEqual(obj.schema_for_id(0x999, self.schemas, {0x999: 7}).key, "door")
        chest = self.schemas["chest"]
        self.assertEqual(chest.field("contents").enum[0], "shotgun (weapon 3)")
        self.assertEqual(chest.defaults()["size"], 100)
        yaw = chest.role("yaw")
        self.assertEqual(obj.value_of_angle(yaw, obj.angle_of(yaw, -0x4000)), -0x4000)
        byte = self.schemas["ammo"].role("yaw")
        self.assertEqual(obj.value_of_angle(byte, obj.angle_of(byte, 0xC0)), 0xC0)
        self.assertAlmostEqual(obj.scale_of(self.schemas["key"].role("scale"), 32), 0.5)

    def test_entry_round_trip(self):
        s = self.schemas["exit"]
        data = obj.encode_entry(0x0B, (-600, 0, 1115), 0x1C, s,
                                {"levelLow": 21, "levelHigh": 255, "startPoint": 0, "radius": 110, "unlockWorld": -1},
                                raw_params=bytes(range(0x12)))
        self.assertEqual(len(data), 0x1C)
        back = obj.decode_entry(data, s)
        self.assertEqual(back["id"], 0x0B)
        self.assertEqual(back["pos"], [-600, 0, 1115])
        self.assertEqual(back["fields"]["unlockWorld"], -1)
        self.assertEqual(back["fields"]["radius"], 110)
        self.assertEqual(back["params"][2 * (0x0D - 0x0A):2 * (0x0E - 0x0A)], "03")  # raw byte kept where no field

    def test_entry_limits(self):
        s = self.schemas["squadron"]
        with self.assertRaises(ValueError):
            obj.encode_entry(0x4F, (0, 0, 0), 0x20, s, {"region": 1})  # +0x3F does not fit 0x20 bytes
        with self.assertRaises(ValueError):
            obj.encode_entry(0x4F, (0, 0, 0), 0x44, s, {"region": 300})
        with self.assertRaises(ValueError):
            obj.encode_entry(0x4F, (0, 0, 0), 0x100, s, {})


class ProjectFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _bytes(self, path: Path) -> dict:
        return {p.relative_to(path).as_posix(): p.read_bytes() for p in sorted(path.rglob("*.json"))}

    def test_load_save_identical(self):
        p = sample_project(self.root)
        before = self._bytes(p.path)
        proj.load(p.path).save()
        self.assertEqual(before, self._bytes(p.path))
        self.assertTrue(all(b.endswith(b"\n") and b"\r" not in b for b in before.values()))

    def test_load_reports_every_problem(self):
        p = sample_project(self.root)
        (p.path / "levels" / "gemini" / "level.json").write_text('{"format": 1}', encoding="utf-8")
        (p.path / "planets" / "gemini.json").write_text("not json", encoding="utf-8")
        with self.assertRaises(proj.ProjectError) as cm:
            proj.load(p.path)
        text = str(cm.exception)
        self.assertIn("level.json", text)
        self.assertIn("gemini.json: not valid JSON", text)

    def test_create_refuses_bad_id_and_non_empty(self):
        with self.assertRaises(proj.ProjectError):
            proj.create(self.root / "a", "Bad Id", "x")
        sample_project(self.root)
        with self.assertRaises(proj.ProjectError):
            proj.create(self.root / "sample.jfgproj", "ok.id", "x")


class Validator(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = sample_project(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def rules(self, p) -> set[tuple[str, str]]:
        return {(x.severity, x.rule) for x in val.validate(p)}

    def errors(self, p) -> list:
        return [x for x in val.validate(p) if x.severity == val.ERROR]

    def objects(self, p, level="gemini"):
        return {o["uid"]: o for o in p.levels[level].objects["objects"]}

    def test_sample_is_clean(self):
        self.assertEqual(self.errors(self.base), [])
        self.assertFalse(any(x.severity == val.WARNING for x in val.validate(self.base)))

    def test_cutscene_minus_one(self):
        p = clone(self.base)
        p.levels["gemini"].level["header"]["cutsceneData"] = -1
        self.assertIn((val.ERROR, "cutscene-data"), self.rules(p))

    def test_squad_region_missing(self):
        p = clone(self.base)
        self.objects(p)["squad1"]["fields"]["region"] = 3
        found = [x for x in self.errors(p) if x.rule == "squad-region"]
        self.assertEqual([x.uid for x in found], ["squad1"])

    def test_region_zero_needs_points_too(self):
        p = clone(self.base)
        objs = p.levels["gemini"].objects["objects"]
        p.levels["gemini"].objects["objects"] = [o for o in objs if not o["uid"].startswith("rp")]
        self.objects(p)["squad1"]["fields"]["region"] = 0
        self.assertIn((val.ERROR, "squad-region"), self.rules(p))

    def test_member_without_squadron(self):
        p = clone(self.base)
        self.objects(p)["ant2"]["fields"]["squadron"] = 9
        self.assertEqual([x.uid for x in self.errors(p) if x.rule == "squad-members"], ["ant2"])

    def test_list_too_big(self):
        p = clone(self.base)
        objs = p.levels["gemini"].objects["objects"]
        objs += [{"uid": f"x{i}", "type": "stock:objtype/0x99", "pos": [0, 0, 0], "size": 0xFF} for i in range(48)]
        self.assertIn((val.ERROR, "list-size"), self.rules(p))

    def test_size_unknown_and_too_small(self):
        p = clone(self.base)
        del self.objects(p)["door1"]["size"]
        self.objects(p)["squad1"]["size"] = 0x30
        bad = {(x.rule, x.uid) for x in self.errors(p)}
        self.assertIn(("size", "door1"), bad)
        self.assertIn(("size", "squad1"), bad)

    def test_exit_target_start_point(self):
        p = clone(self.base)
        self.objects(p)["exit0"]["fields"]["startPoint"] = 4
        self.assertIn(("exit-target", "exit0"), {(x.rule, x.uid) for x in self.errors(p)})
        self.objects(p)["exit0"]["target"] = "pack:level/nowhere"
        self.assertIn("no such level", " ".join(x.message for x in self.errors(p)))

    def test_moon_disc_and_world(self):
        p = clone(self.base)
        del p.planets["gemini"]["disc"]["orbitParent"]
        p.planets["gemini"]["replaces"] = "stock:mappoint/6"  # the rule is for take-overs
        p.levels["gemini"].level["header"]["world"] = 16
        rules = self.rules(p)
        self.assertIn((val.ERROR, "moon-disc"), rules)
        self.assertIn((val.ERROR, "world"), rules)

    def test_route_direction_used_twice(self):
        p = clone(self.base)
        p.planets["other"] = dict(p.planets["gemini"], fixedWorld=14,
                                  disc=dict(p.planets["gemini"]["disc"], index=9))
        self.assertEqual([x.rule for x in self.errors(p)], ["route"])

    def test_header_must_set(self):
        p = clone(self.base)
        del p.levels["gemini"].level["header"]["tune"]
        p.levels["gemini"].level["header"]["regionIndex"] = 40
        self.assertEqual([x.rule for x in self.errors(p)], ["header", "header"])

    def test_two_doors_one_lock(self):
        p = clone(self.base)
        p.levels["gemini"].objects["objects"].append(dict(self.objects(p)["door1"], uid="door2"))
        self.assertIn((val.ERROR, "door-link"), self.rules(p))

    def test_stock_squadron_region_and_members(self):
        p = clone(self.base)
        squad = self.objects(p)["squad1"]
        del squad["fields"]["region"]
        squad["stock"] = {"list": 424, "entry": 260}
        p.levels["gemini"].objects["objects"] = [o for o in p.levels["gemini"].objects["objects"]
                                                 if not o["uid"].startswith("ant")]
        rules = self.rules(p)
        self.assertIn((val.INFO, "squad-region"), rules)
        self.assertIn((val.INFO, "squad-members"), rules)
        self.assertFalse(self.errors(p))

    def test_missing_track(self):
        p = clone(self.base)
        p.levels["gemini"].level["track"] = "pack:track/none"
        self.assertIn((val.ERROR, "track"), self.rules(p))


class Cli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["JFGSTUDIO_CONFIG_DIR"] = self.tmp.name

    def tearDown(self):
        del os.environ["JFGSTUDIO_CONFIG_DIR"]
        self.tmp.cleanup()

    def test_new_validate(self):
        path = str(Path(self.tmp.name) / "p.jfgproj")
        self.assertEqual(cli.main(["new", path, "--id", "me.p", "--name", "P"]), 0)
        self.assertEqual(cli.main(["validate", path]), 0)
        sample = sample_project(Path(self.tmp.name))
        sample.levels["gemini"].level["header"]["cutsceneData"] = -1
        sample.save()
        self.assertEqual(cli.main(["validate", str(sample.path)]), 1)

    def test_settings(self):
        self.assertEqual(cli.main(["settings", "set", "decomp", "C:/decomp"]), 0)
        self.assertEqual(settings.get("decomp"), "C:/decomp")
        self.assertEqual(cli.main(["settings", "set", "nope", "x"]), 1)


if __name__ == "__main__":
    unittest.main()


class CrashLog(unittest.TestCase):
    def test_write(self):
        from jfgstudio import crashlog
        with tempfile.TemporaryDirectory() as tmp:
            old = os.environ.get("JFGSTUDIO_DATA_DIR")
            os.environ["JFGSTUDIO_DATA_DIR"] = tmp
            try:
                try:
                    raise ValueError("boom")
                except ValueError as e:
                    path = crashlog.write(e, "a test")
                text = path.read_text(encoding="utf-8")
            finally:
                if old is None:
                    del os.environ["JFGSTUDIO_DATA_DIR"]
                else:
                    os.environ["JFGSTUDIO_DATA_DIR"] = old
        self.assertIn("where: a test", text)
        self.assertIn("ValueError: boom", text)
