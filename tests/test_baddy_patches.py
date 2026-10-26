"""Patches of the enemy animation tables (baddies.json) on made-up tables (no ROM): applying them, the overfull check,
and the project file's round trip."""

import tempfile
import unittest

from jfgstudio import baddy_patches as bp
from jfgstudio import project as proj


class FakeBaddies:  # jfgformats.baddies' check, as the build calls it
    PICK_LIST = 16

    @staticmethod
    def overfull_actions(anims):
        count = {}
        for a in anims:
            for act in {a["action"], a["altAction"]} - {0}:
                count[act] = count.get(act, 0) + 1
        return {act: n for act, n in count.items() if n > FakeBaddies.PICK_LIST}


def _anim(action):
    return {"action": action, "altAction": 0, "weight": 1, "stance": 0, "fireSet": None, "loops": 0}


ROWS = [{"row": 0, "anims": [_anim(1) for _ in range(18)]}, {"row": 1, "anims": [_anim(2), _anim(3)]}]


class Patches(unittest.TestCase):
    def test_applied(self):
        rows = bp.applied(ROWS, [{"row": 1, "anim": 1, "set": {"fireSet": 4}}])
        self.assertEqual(rows[1]["anims"][1]["fireSet"], 4)
        self.assertIsNone(ROWS[1]["anims"][1]["fireSet"])  # the stock rows are left alone

    def test_overfull_until_fixed(self):
        one = [{"row": 0, "anim": 0, "set": {"action": 53}}]
        self.assertTrue(bp.problems(ROWS, one, FakeBaddies))  # 17 left on action 1
        two = one + [{"row": 0, "anim": 1, "set": {"action": 53}}]
        self.assertEqual(bp.problems(ROWS, two, FakeBaddies), [])
        self.assertEqual(bp.problems(ROWS, [], FakeBaddies), [])  # a set the pack doesn't touch is the game's

    def test_entry_past_its_set(self):
        self.assertIn("no anim 5", bp.problems(ROWS, [{"row": 1, "anim": 5, "set": {"loops": 1}}], FakeBaddies)[0])

    def test_check_set(self):
        bp.check_set({"fireSet": None, "weight": 31})
        for bad in ({"weight": 32}, {"speed": 1}, {"stance": True}):
            with self.assertRaises(bp.PatchError):
                bp.check_set(bad)


class ProjectFile(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            p = proj.create(f"{d}/x.jfgproj", "test.x", "X")
            p.baddies.append({"row": 17, "anim": 16, "expect": {"action": 1}, "set": {"action": 53}})
            p.save()
            self.assertEqual(proj.load(f"{d}/x.jfgproj").baddies, p.baddies)
            p.baddies.clear()
            p.save()  # every patch removed: no file
            self.assertEqual(proj.load(f"{d}/x.jfgproj").baddies, [])


class ModIds(unittest.TestCase):
    def test_dependency_ids(self):
        from jfgstudio import agent
        from jfgstudio import validate as val
        with tempfile.TemporaryDirectory() as d:
            proj.create(f"{d}/lib.jfgproj", "yourname.library", "Library")
            s = agent.Session(f"{d}/lib.jfgproj")
            with self.assertRaises(agent.AgentError):
                s.add_dependency("someone.base", "1.0.0")  # the loader refuses a dependency id with a dot
            self.assertEqual(s.add_dependency("someone_base", "1.0.0")["dependencies"][0]["id"], "someone_base")
            rules = {p.rule: p.severity for p in val.validate(s.p)}
            self.assertEqual(rules.get("mod-id"), val.WARNING)  # its own id has a dot: nothing can depend on it
            s.p.meta["dependencies"].append({"id": "someone.else", "version": "1.0.0"})  # written by hand
            self.assertIn(("dependency-id", val.ERROR), {(p.rule, p.severity) for p in val.validate(s.p)})


if __name__ == "__main__":
    unittest.main()
