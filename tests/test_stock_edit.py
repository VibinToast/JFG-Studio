import os
import struct
import tempfile
import unittest
from pathlib import Path

from jfgstudio import agent, edit, stock_edit
from jfgstudio import objects as obj
from jfgstudio import project as proj
from tests.fixtures import sample_project

HEALTH, AMMO = 0xA9, 0xE8


def _entry(oid, pos, size=0x10, params=b""):
    body = bytearray(size)
    struct.pack_into(">HBBhhh", body, 0, oid, size, 0, *pos)
    body[obj.PARAMS_OFFSET:obj.PARAMS_OFFSET + len(params)] = params
    return bytes(body)


class GameData:
    """A made-up game level 3 with object lists 7 (slot 0) and 8 (slot 1); list 8 is level 4's too."""

    LISTS = {7: [(HEALTH, _entry(HEALTH, (100, 0, 50), params=b"\x01"))],
             8: [(AMMO, _entry(AMMO, (0, 0, 0))), None, (HEALTH, _entry(HEALTH, (-200, 10, 300)))]}

    def levels(self):
        out = [{"id": i, "name": f"Place {i}", "world": 0, "track": i, "objectList0": 0, "objectList1": 1}
               for i in range(6)]
        out[3].update(objectList0=7, objectList1=8)
        out[4].update(objectList0=9, objectList1=8)
        return out

    def objlist(self, n):
        return self.LISTS.get(n, [])

    def stock_entry(self, list_id, index):
        e = self.LISTS[list_id][index]
        if e is None:
            raise IndexError(index)
        return e[1]


class _GameLevel(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_config = os.environ.get("JFGSTUDIO_CONFIG_DIR")
        os.environ["JFGSTUDIO_CONFIG_DIR"] = str(Path(self.tmp.name) / "config")
        self.p = sample_project(Path(self.tmp.name))
        proj.edit_stock_level(self.p, "portal", 3, "Portal", 3)
        self.p.save()
        self.level = self.p.levels["portal"]
        self.data = GameData()

    def tearDown(self):
        if self.old_config is None:
            del os.environ["JFGSTUDIO_CONFIG_DIR"]
        else:
            os.environ["JFGSTUDIO_CONFIG_DIR"] = self.old_config
        self.tmp.cleanup()


class StockEdits(_GameLevel):
    def test_live_objects_keep_only_changes(self):
        live = stock_edit.materialize(self.level, self.data)
        self.assertEqual([o["uid"] for o in live], ["stock:7:0", "stock:8:0", "stock:8:2"])  # the gap is skipped
        h = edit.History(self.level)
        h.set_value("stock:8:2", ("pos",), [-150, 10, 300], "move")
        h.set_value("stock:7:0", ("fields", "amount"), 2, "set amount")
        h.delete({"stock:8:0"})
        self.assertEqual([o["uid"] for o in self.level.objects["objects"]], [])  # the level's own: untouched
        self.p.save()
        edits = proj.load(self.p.path).levels["portal"].objects["stockEdits"]
        self.assertEqual(edits, [{"list": 7, "index": 0, "id": HEALTH, "fields": {"amount": 2}},
                                 {"list": 8, "index": 0, "id": AMMO, "deleted": True},
                                 {"list": 8, "index": 2, "id": HEALTH, "pos": [-150, 10, 300]}])
        again = proj.load(self.p.path).levels["portal"]
        live = {o["uid"]: o for o in stock_edit.materialize(again, self.data)}
        self.assertEqual(live["stock:8:2"]["pos"], [-150, 10, 300])
        self.assertTrue(live["stock:8:0"]["deleted"])
        h.undo()  # the removal back: the change list shrinks on save
        h.undo()
        h.undo()
        self.p.save()
        self.assertNotIn("stockEdits", proj.load(self.p.path).levels["portal"].objects)

    def test_patch_operations(self):
        schemas = obj.load_schemas()
        original = self.data.stock_entry(8, 2)
        ops = stock_edit.patch_ops({"list": 8, "index": 2, "id": HEALTH, "pos": [-150, 10, 300]}, original, schemas)
        self.assertEqual(ops, [{"op": "set", "index": 2, "id": HEALTH, "at": 5, "hex": "6a"}])  # x's low byte
        ops = stock_edit.patch_ops({"list": 7, "index": 0, "id": HEALTH, "fields": {"amount": 2}},
                                   self.data.stock_entry(7, 0), schemas)
        self.assertEqual(ops, [{"op": "set", "index": 0, "id": HEALTH, "at": 0x0A, "hex": "02"}])
        self.assertEqual(stock_edit.patch_ops({"list": 8, "index": 0, "id": AMMO, "deleted": True},
                                              self.data.stock_entry(8, 0), schemas),
                         [{"op": "delete", "index": 0, "id": AMMO}])
        self.assertEqual(stock_edit.patch_ops({"list": 8, "index": 2, "id": HEALTH, "pos": [-200, 10, 300]},
                                              original, schemas), [])  # nothing changed
        self.assertEqual(stock_edit.sharing(self.data, 3, 8), [4])

    def test_duplicate_makes_an_object_of_the_level(self):
        stock_edit.materialize(self.level, self.data)
        h = edit.History(self.level)
        (copy,) = h.duplicate({"stock:8:2"})
        o = edit.find(self.level, copy)
        self.assertEqual((o["stock"], o["pos"]), ({"list": 8, "entry": 2}, [-180, 10, 320]))
        self.assertNotIn("game", o)
        self.assertFalse(copy.startswith("stock:"))


class AgentGameObjects(_GameLevel):
    def test_read_only_until_unlocked(self):
        s = agent.Session()
        s._catalog_error = "tests have no catalog"
        s.open(self.p.path)
        s._catalog, s._catalog_error = self.data, None
        s._types = {}
        listed = s.game_objects("portal")
        self.assertFalse(listed["editable"])
        self.assertEqual([o["uid"] for o in listed["objects"]], ["stock:7:0", "stock:8:0", "stock:8:2"])
        self.assertIn("level(s) 4", listed["note"])
        with self.assertRaises(agent.AgentError):
            s.update_object("portal", "stock:8:2", pos=[0, 0, 0], on_floor=False)
        with self.assertRaises(agent.AgentError):
            s.delete_objects("portal", ["stock:8:0"])
        s.edit_game_objects("portal", True)
        s.update_object("portal", "stock:8:2", pos=[-150, 10, 300], on_floor=False)
        s.delete_objects("portal", ["stock:8:0"])
        changed = {o["uid"]: o for o in s.game_objects("portal", include_removed=True)["objects"]}
        self.assertTrue(changed["stock:8:2"]["changed"])
        self.assertTrue(changed["stock:8:0"]["removed"])
        s.revert_game_objects("portal")
        self.assertNotIn("stockEdits", proj.load(self.p.path).levels["portal"].objects)
        s.edit_game_objects("portal", False)  # locked again
        with self.assertRaises(agent.AgentError):
            s.update_object("portal", "stock:8:2", pos=[0, 0, 0], on_floor=False)
