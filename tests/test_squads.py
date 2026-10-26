import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from jfgstudio import edit, squads
from jfgstudio import objects as obj
from jfgstudio import prefabs as pre
from jfgstudio.gui import level_tools
from tests.fixtures import sample_project

# made-up stock squads of 0x29: (mode, moves, member entry, (list, entry), behaviour)
INDEX = {0x29: [(2, True, 4, (10, 2), 0x11), (6, True, 5, (13, 3), 2)]}


class FakeState:
    """The editor's state, as much of it as the Patrol tool and button use."""

    def __init__(self, level):
        self.schemas = obj.load_schemas()
        self.behaviours = {}
        self.cat = object()  # never read: the squad index is given
        self.squads = INDEX
        self.objtypes = {pre.PATROL_POINT_ID: {"examples": [[12, 1]]}, pre.PATROL_PATH_ID: {"examples": [[12, 2]]}}
        self.h = edit.History(level)
        self.message = ""
        self.selection = set()
        self.patrol_last = None

    def history(self):
        return self.h

    def catalog(self):
        return self.cat

    def schema(self, o):
        return obj.schema_for(o, self.schemas, self.behaviours)

    def validate(self):
        pass


class Patrol(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = sample_project(Path(self.tmp.name))
        self.level = self.p.levels["gemini"]
        self.sq = edit.find(self.level, "squad1")
        self.sq["fields"].update({"baddyType": 0x29, "kind": 0x12, "trigger": 1})
        self.S = FakeState(self.level)
        self.app = mock.patch.object(level_tools, "_app", lambda: SimpleNamespace(S=self.S))
        self.app.start()

    def tearDown(self):
        self.app.stop()
        self.tmp.cleanup()

    def test_patrol_changes(self):
        key = lambda o: (s := self.S.schema(o)) and s.key  # noqa: E731
        self.level_before = [dict(o.get("fields", {})) for o in self.level.objects["objects"]]
        self.S.h.do(edit.Command("patrol", squads.patrol(self.level, self.sq, INDEX, key)))
        f = self.sq["fields"]
        self.assertEqual(f["kind"], 0x16)  # mode 6, the high bits kept
        self.assertEqual((f["behaviour"], f["alertBehaviour"], f["hurtBehaviour"]), (2, 0x11, 0x11))
        self.assertEqual(self.sq["stock"], {"list": 13, "entry": 3})  # the game's patrol squad of its enemy
        self.assertEqual(f["trigger"], pre.TRIGGER_AT_ONCE)
        self.assertEqual({edit.find(self.level, u)["stock"]["entry"] for u in ("ant1", "ant2")}, {5})
        self.S.h.undo()  # one undo step
        self.assertEqual([o.get("fields", {}) for o in self.level.objects["objects"]], self.level_before)
        self.assertNotIn("stock", self.sq)

    def test_no_patrol_squad_of_its_enemy_keeps_its_own(self):
        key = lambda o: (s := self.S.schema(o)) and s.key  # noqa: E731
        index = {0x29: INDEX[0x29][:1]}
        changes = squads.patrol(self.level, self.sq, index, key)
        self.assertEqual({c.path for c in changes}, {("fields", n) for n in
                                                     ("kind", "behaviour", "alertBehaviour", "hurtBehaviour")})

    def test_button_warns_without_a_patrol_point_near(self):
        level_tools.make_patrol(self.sq)
        self.assertEqual(self.sq["fields"]["behaviour"], squads.PATROL_BEHAVIOUR)
        self.assertIn("No patrol point within 300", self.S.message)
        self.assertEqual(len(self.S.h.done), 1)
        x, y, z = self.sq["pos"]
        level_tools.patrol_point([x + 200, y, z], None)
        level_tools.make_patrol(self.sq)
        self.assertNotIn("No patrol point", self.S.message)

    def test_patrol_tool_never_sets_a_start_value(self):
        level_tools.patrol_point([0, 0, 0], None)
        first = edit.find(self.level, self.S.patrol_last)
        level_tools.patrol_point([300, 0, 0], first)
        points = [o for o in self.level.objects["objects"] if (s := self.S.schema(o)) and s.key == "patrolpoint"]
        self.assertEqual(len(points), 2)
        self.assertEqual([o["fields"]["startAtLoad"] for o in points], [0, 0])  # a start value rides a lift
        links = [o for o in self.level.objects["objects"] if (s := self.S.schema(o)) and s.key == "patrolpath"]
        self.assertEqual(len(links), 1)


class StandIn(unittest.TestCase):
    # made-up squads: 0x30 walks (two smart squads), 0x31 walks (a sentry), 0x40 flies; 0x50 walks and has none
    SQUADS = {0x30: [(2, True, 4, (10, 2), 0x11), (2, True, 6, (10, 5), 0x11)], 0x31: [(2, False, 7, (11, 1), 9)],
              0x40: [(2, True, None, (12, 0), 5)]}
    MOVES = {0x30: 1, 0x31: 1, 0x40: 5, 0x50: 1, 0x51: 5}

    def test_same_movement(self):
        walk = squads.stand_in(self.SQUADS, 0x50, self.MOVES.get)
        self.assertEqual(walk[2], 0x30)  # a smart walker, not the sentry
        fly = squads.stand_in(self.SQUADS, 0x51, self.MOVES.get)
        self.assertEqual((fly[0], fly[1], fly[2]), ((12, 0), None, 0x40))  # a flyer: its squadron spawns its own
        self.assertIsNone(squads.stand_in(self.SQUADS, 0x52, self.MOVES.get))  # not a baddy

    def test_size(self):
        self.assertEqual(squads.stand_in_size(0x1E5, 40, (145, 190, 215), (116, 133, 53)), 26)  # play-tested
        self.assertEqual(squads.stand_in_size(0x50, 40, (100, 200, 100), (50, 100, 50)), 80)  # as large as the template
        self.assertEqual(squads.stand_in_size(0x50, 0, None, None), 100)  # 0 is 1.0
        self.assertEqual(squads.stand_in_size(0x50, 200, (500,), (10,)), 255)
