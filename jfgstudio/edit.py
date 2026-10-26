"""Editing a project: every change is a command on a per-level undo history (task S15).

Commands work on the project's own dicts, so the editor, the CLI and tests share them. A gizmo drag changes an object
live and records one command when it ends (`History.record`), so a drag is one undo step.
"""

import copy
import itertools
from dataclasses import dataclass, field
from pathlib import Path

from jfgstudio.project import Level

_uid_counter = itertools.count(1)
LEVEL = "@level"  # a Change on the level's own settings (level.json) instead of an object


def new_uid(level: Level, stem: str) -> str:
    """A uid not used in the level, from a readable stem (`ant`, `exit`...)."""
    used = {o["uid"] for o in level.objects["objects"]}
    stem = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in stem.lower()) or "obj"
    for k in itertools.count(1):
        uid = f"{stem}{k}"
        if uid not in used:
            return uid
    raise AssertionError


def find(level: Level, uid: str) -> dict | None:
    """An object by uid, or the level's settings (level.json) for LEVEL."""
    if uid == LEVEL:
        return level.level
    if uid.startswith("stock:"):  # one of the game level's own objects (stock_edit), while it is live
        return next((o for o in level.__dict__.get("stock_live") or () if o["uid"] == uid), None)
    return next((o for o in level.objects["objects"] if o["uid"] == uid), None)


def _get(obj: dict, path: tuple):
    cur = obj
    for p in path:
        if not isinstance(cur, dict) or p not in cur:
            return None
        cur = cur[p]
    return copy.deepcopy(cur)


def _set(obj: dict, path: tuple, value) -> None:
    cur = obj
    for p in path[:-1]:
        cur = cur.setdefault(p, {})
    if value is None:
        cur.pop(path[-1], None)
    else:
        cur[path[-1]] = copy.deepcopy(value)


@dataclass
class Change:
    """One value of one object: path ("pos",), ("fields", "doorId"), ("name",) ...; None = absent."""
    uid: str
    path: tuple
    old: object
    new: object


@dataclass
class Command:
    label: str
    changes: list[Change] = field(default_factory=list)
    added: list[tuple[int, dict]] = field(default_factory=list)    # (index, object) put in
    removed: list[tuple[int, dict]] = field(default_factory=list)  # (index, object) taken out
    files: list[tuple[str, bytes | None, bytes]] = field(default_factory=list)  # (path, old or None, new): geometry
    merge: bool = False  # later edits of the same values join this step (set_value merge)

    def apply(self, level: Level, forward: bool) -> None:
        for path, old, new in self.files:
            data = new if forward else old
            if data is None:
                Path(path).unlink(missing_ok=True)
            else:
                Path(path).write_bytes(data)
        objs = level.objects["objects"]
        if forward:
            for i, _ in sorted(self.removed, key=lambda x: -x[0]):
                del objs[i]
            for i, o in sorted(self.added, key=lambda x: x[0]):
                objs.insert(i, copy.deepcopy(o))
        else:
            for i, _ in sorted(self.added, key=lambda x: -x[0]):
                del objs[i]
            for i, o in sorted(self.removed, key=lambda x: x[0]):
                objs.insert(i, copy.deepcopy(o))
        for c in self.changes if forward else reversed(self.changes):
            o = find(level, c.uid)
            if o is not None:
                _set(o, c.path, c.new if forward else c.old)


class History:
    """Undo and redo for one level."""

    def __init__(self, level: Level):
        self.level = level
        self.done: list[Command] = []
        self.undone: list[Command] = []
        self.dirty = False

    def do(self, cmd: Command) -> None:
        cmd.apply(self.level, True)
        self.record(cmd)

    def record(self, cmd: Command) -> None:
        """A command whose effect is already in the level (a finished gizmo drag)."""
        if not (cmd.changes or cmd.added or cmd.removed or cmd.files):
            return
        self.done.append(cmd)
        self.undone.clear()
        self.dirty = True

    def undo(self) -> str | None:
        if not self.done:
            return None
        cmd = self.done.pop()
        cmd.apply(self.level, False)
        self.undone.append(cmd)
        self.dirty = True
        return cmd.label

    def redo(self) -> str | None:
        if not self.undone:
            return None
        cmd = self.undone.pop()
        cmd.apply(self.level, True)
        self.done.append(cmd)
        self.dirty = True
        return cmd.label

    # the commands the editor uses
    def set_value(self, uid: str, path: tuple, value, label: str | None = None, merge: bool = False) -> None:
        """One value as one undo step. With `merge`, a run of edits of the same value (a slider or colour being
        dragged) stays one step."""
        o = find(self.level, uid)
        if o is None:
            return
        old = _get(o, path)
        if old == value:
            return
        label = label or f"set {'.'.join(path)}"
        last = self.done[-1] if self.done else None
        same = last is not None and last.label == label and all(c.uid == uid for c in last.changes)
        if merge and same and last.merge and not self.undone:
            c = next((c for c in last.changes if c.path == path), None)
            if c is None:
                last.changes.append(Change(uid, path, old, value))
            else:
                c.new = value
            _set(o, path, value)
            self.dirty = True
            return
        cmd = Command(label, [Change(uid, path, old, value)])
        cmd.merge = merge
        self.do(cmd)

    def write_file(self, path, data: bytes, label: str) -> None:
        """Replaces a project file (a geometry .glb) as one undo step."""
        path = Path(path)
        old = path.read_bytes() if path.exists() else None
        self.do(Command(label, files=[(str(path), old, data)]))

    def move(self, moves: dict, label: str = "move") -> None:
        """moves: uid -> (old pos, new pos); the new positions are already in the level."""
        self.record(Command(label, [Change(uid, ("pos",), list(a), list(b)) for uid, (a, b) in moves.items()
                                    if list(a) != list(b)]))

    def add(self, objs: list[dict], label: str = "add") -> None:
        n = len(self.level.objects["objects"])
        self.do(Command(label, added=[(n + k, o) for k, o in enumerate(objs)]))

    def delete(self, uids: set, label: str = "delete") -> None:
        """The level's own objects are taken out; the game level's (stock_edit) are marked removed."""
        objs = self.level.objects["objects"]
        removed = [(i, copy.deepcopy(o)) for i, o in enumerate(objs) if o["uid"] in uids]
        game = [Change(u, ("deleted",), None, True) for u in sorted(uids) if u.startswith("stock:")
                and (o := find(self.level, u)) is not None and not o.get("deleted")]
        self.do(Command(label, game, removed=removed))

    def duplicate(self, uids: set, offset=(20, 0, 20)) -> list[str]:
        copies = []
        game = [o for o in self.level.__dict__.get("stock_live") or () if o["uid"] in uids]
        for o in [*self.level.objects["objects"], *game]:
            if o["uid"] in uids:
                c = copy.deepcopy(o)
                for k in ("game", "deleted"):  # a copy of a game object is one of the level's own
                    c.pop(k, None)
                stem = o.get("name") or "copy" if o["uid"].startswith("stock:") else o["uid"].rstrip("0123456789")
                c["uid"] = new_uid(self.level, stem or "obj")
                c["pos"] = [p + d for p, d in zip(o["pos"], offset, strict=True)]
                copies.append(c)
                self.level.objects["objects"].append(c)  # so the next new_uid sees it
        for _ in copies:
            self.level.objects["objects"].pop()
        self.add(copies, "duplicate")
        return [c["uid"] for c in copies]
