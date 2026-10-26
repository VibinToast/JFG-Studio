"""Editing a game level's own objects, in a level of the project that edits it (a stock-level edit).

The game level's objects come from the player's catalog each time; the project keeps only what was changed
(objects.json "stockEdits": an object moved, its fields set, renamed, or removed), never the game's own data. While
the project is open they are live objects (uids "stock:<list>:<index>") that the undo history and the editor work on
like the level's own. They are read-only unless the level's "editStock" is on; the level's own added objects are
unaffected either way. The build turns the changes into the port's object-list patches ("set" and "delete", each
checked against the object's id: build.py), so a pack carries its changes only.
"""

import struct

from jfgstudio import objects as obj

PREFIX = "stock:"


def uid(list_id: int, index: int) -> str:
    return f"{PREFIX}{list_id}:{index}"


def is_stock(uid_: str) -> bool:
    return uid_.startswith(PREFIX)


def editable(level) -> bool:
    """The game level's objects may be changed (the level's "editStock")."""
    return bool(level.level.get("editStock"))


def materialize(level, catalog, refresh: bool = False) -> list[dict]:
    """The game level's objects as live objects, with the project's changes on them (cached on the level). Empty for
    a level of the pack's own or without a catalog."""
    if level.base is None or catalog is None:
        return []
    live = level.__dict__.get("stock_live")
    if live is not None and not refresh:
        return live
    edits = {(e["list"], e["index"]): e for e in level.objects.get("stockEdits", [])}
    info = catalog.levels()[level.base]
    live = []
    for n in sorted({info.get("objectList0"), info.get("objectList1")} - {None}):
        try:
            entries = catalog.objlist(n)
        except (IndexError, ValueError, AttributeError):  # no such list (or a catalog without lists)
            continue
        for i, e in enumerate(entries):
            if e is None:
                continue
            oid, raw = e
            pos = list(obj.decode_entry(raw)["pos"])
            o = {"uid": uid(n, i), "type": f"stock:objtype/0x{oid:X}", "pos": list(pos), "fields": {},
                 "stock": {"list": n, "entry": i}, "game": {"list": n, "index": i, "id": oid, "pos": pos}}
            ed = edits.get((n, i))
            if ed is not None and ed.get("id") == oid:  # made for this data: else the game's own (sync drops it)
                if "pos" in ed:
                    o["pos"] = list(ed["pos"])
                o["fields"] = dict(ed.get("fields", {}))
                if ed.get("name"):
                    o["name"] = ed["name"]
                if ed.get("deleted"):
                    o["deleted"] = True
            live.append(o)
    level.__dict__["stock_live"] = live
    return live


def live(level) -> list[dict]:
    """The live game objects already made (materialize), or none."""
    return level.__dict__.get("stock_live") or []


def changed(o: dict) -> bool:
    return bool(o.get("deleted") or o.get("fields") or o.get("name") or list(o["pos"]) != o["game"]["pos"])


def sync(level) -> None:
    """Writes the live game objects' changes into the level's objects.json data ("stockEdits")."""
    if "stock_live" not in level.__dict__:
        return
    edits = []
    for o in level.__dict__["stock_live"]:
        if not changed(o):
            continue
        g = o["game"]
        e = {"list": g["list"], "index": g["index"], "id": g["id"]}
        if list(o["pos"]) != g["pos"]:
            e["pos"] = [round(v) for v in o["pos"]]
        if o.get("fields"):
            e["fields"] = dict(o["fields"])
        if o.get("name"):
            e["name"] = o["name"]
        if o.get("deleted"):
            e["deleted"] = True
        edits.append(e)
    if edits:
        level.objects["stockEdits"] = edits
    else:
        level.objects.pop("stockEdits", None)


def revert(o: dict) -> None:
    """Back to the game's own: position, fields and name, and not removed."""
    o["pos"] = list(o["game"]["pos"])
    o["fields"] = {}
    o.pop("name", None)
    o.pop("deleted", None)


def patch_ops(edit: dict, original: bytes, schemas: dict) -> list[dict]:
    """The port's object-list operations for one edit ("stockEdits" entry) of an entry whose bytes are `original`:
    a "delete", or a "set" of the bytes it changes (with the object id, so the port skips data it wasn't made for)."""
    index, oid = edit["index"], edit["id"]
    if edit.get("deleted"):
        return [{"op": "delete", "index": index, "id": oid}]
    new = bytearray(original)
    if "pos" in edit:
        struct.pack_into(">hhh", new, 4, *[int(round(v)) for v in edit["pos"]])
    schema = obj.schema_for({"type": f"stock:objtype/0x{oid:X}"}, schemas)
    for name, value in edit.get("fields", {}).items():
        f = schema.field(name) if schema else None
        if f is None:
            raise ValueError(f"object {index}: unknown field {name!r}")
        if f.end > len(new):
            raise ValueError(f"object {index}: field {name} is past the end of its entry ({len(new)} bytes)")
        struct.pack_into(obj._FORMATS[f.type], new, f.offset, int(value))
    diff = [k for k in range(len(new)) if new[k] != original[k]]
    if not diff:
        return []
    first, last = diff[0], diff[-1]
    return [{"op": "set", "index": index, "id": oid, "at": first, "hex": bytes(new[first:last + 1]).hex()}]


def sharing(catalog, level_id: int, list_id: int) -> list[int]:
    """The other game levels that use object list `list_id`: editing its objects changes them there too."""
    return [lv["id"] for lv in catalog.levels() if lv["id"] != level_id
            and list_id in (lv.get("objectList0"), lv.get("objectList1"))]
