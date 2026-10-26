"""Squadrons set up to behave: a stock squad of their own enemy to copy their unnamed bytes from, and patrol mode. The
agent (fix_squad, place_patrol) and the editor's Inspector (Patrol) both use these, so a squad set up either way is the
same. Each function returns the edit.Changes to make; the caller does them as one undo step."""

import copy
import math
import struct
from collections import Counter

from jfgstudio import edit, prefabs

PATROL_NEAR = 300  # a squadron farther than this from every patrol point never finds the network
PATROL_BEHAVIOUR = 2  # squadron +0x0D: walk the patrol nodes (MSR mod #6)
SMART, SENTRY = 0x11, 9  # squadron behaviours (+0x0D): walks to the hero in its region; never walks (MSR mod #6)
PATROL_MODE = 6  # a squadron's kind (low 4 bits) that walks the patrol network (decomp: "Squad patrol network")


def index(catalog) -> dict:
    """Every stock squadron by its enemy: [(mode, moves, member entry or None, (list, entry), behaviour)]."""
    out = {}
    try:
        lv = catalog.levels_mod
        lists = catalog.pair(lv.OBJLIST_TABLE, lv.OBJLIST_DATA)
    except Exception:  # noqa: BLE001 (no lists to read: no matches)
        lists = []
    for n, raw in enumerate(lists):
        entries = lv.objlist_entries(raw)
        for i, e in enumerate(entries):
            if not e or e[0] != prefabs.SQUADRON_ID or len(e[1]) < 0x40:
                continue
            data = e[1]
            baddy, sid = struct.unpack_from(">H", data, 0x36)[0], data[0x2D]
            member = next((j for j, m in enumerate(entries) if m and m[0] == baddy and len(m[1]) > 0x11
                           and m[1][0x11] == sid), None)
            moves = struct.unpack_from(">b", data, 0x38)[0] > 0
            out.setdefault(baddy, []).append((data[0x24] & 0xF, moves, member, (n, i), data[0x0D]))
    return out


def examples(squads: dict, member_oid: int, mode: int | None = None):
    """(squadron entry, member entry) of a stock squad of `member_oid` in `squads` (index()), [list, entry] each (the
    member None when its members aren't list entries). A squadron's unnamed bytes (behaviour, missions, hearing ...)
    must come from a squadron of the same enemy (decomp MSR, "Squadron entry"), and in the mode it is used in: `mode`
    (kind's low 4 bits: 6 patrol), else the enemy's usual one (2 in 893 of the game's 924 squads; a squad copied from a
    rare mode-1 one ran into place and crouched without firing, play-tested). Of those, a smart one (behaviour 0x11:
    walks inside its region to the hero) before any other, and a sentry (9: never walks, faces one way) last. None
    when the game has no squad of that enemy in that mode."""
    found = squads.get(member_oid, [])
    if not found:
        return None
    if mode is None:
        mode = Counter(f[0] for f in found).most_common(1)[0][0]
    same = [f for f in found if f[0] == mode]
    if not same:
        return None
    best = max(same, key=lambda f: (f[4] == SMART, f[4] != SENTRY, f[1], f[2] is not None))
    return best[3], (best[3][0], best[2]) if best[2] is not None else None


def stand_in(squads: dict, member_oid: int, move_type) -> tuple | None:
    """For an enemy the game has no squad of (a cut enemy): (squadron entry, member entry, the enemy copied) of a
    squad of another enemy that moves the same way (`move_type(oid)`: the baddy row's moveType, walking or flying),
    ranked as examples() ranks them; None when no enemy moves that way. Not the first squadron in the lists: that
    one is a friendly villager squad at scale 26 (it left a cut worm tiny and an alien invisible)."""
    want = move_type(member_oid)
    if want is None:
        return None
    best = None
    for oid in sorted(squads):
        if oid == member_oid or move_type(oid) != want:
            continue
        found = examples(squads, oid)
        if found is None:
            continue
        f = next(f for f in squads[oid] if f[3] == found[0])
        rank = (f[4] == SMART, f[4] != SENTRY, f[1], f[2] is not None, len(squads[oid]))
        if best is None or rank > best[0]:
            best = (rank, found, oid)
    return (*best[1], best[2]) if best else None


def native_size(scale: float | None) -> int:
    """A squadron's size field (hundredths; it replaces the members' definition scale) that draws an enemy at its
    own definition's scale."""
    return max(1, min(255, round((scale or 1.0) * 100)))


def fix(level, sq: dict, squads: dict, key, mode: int | None = None) -> tuple[list[edit.Change], dict]:
    """The changes that point a squadron (and its members) at a stock squad of its own enemy, keeping every field
    set, make it spawn at once (a stock squad's trigger zone is where it stood in its own level) and wind its region
    counter-clockwise. `mode`: the mode it will run in (default its kind's). `key(o)`: an object's schema key. Raises
    ValueError when the game has no squad of its enemy."""
    f = sq.get("fields", {})
    oid = f.get("baddyType")
    if mode is None and f.get("kind") is not None:
        mode = f["kind"] & 0xF
    matched = examples(squads, oid, mode) if oid is not None else None
    if matched is None and mode is not None and oid is not None:
        matched = examples(squads, oid)  # no stock squad in its mode (a patrol): its enemy's usual one
    if matched is None:
        raise ValueError(f"the game has no squad of 0x{oid or 0:X} to copy from")
    uid = sq["uid"]
    changes = [edit.Change(uid, ("stock",), copy.deepcopy(sq.get("stock")),
                           {"list": matched[0][0], "entry": matched[0][1]})]
    members = [o for o in level.objects["objects"] if key(o) == "squadmember"
               and o.get("fields", {}).get("squadron") == f.get("squadronId")]
    if matched[1] is not None:
        changes += [edit.Change(m["uid"], ("stock",), copy.deepcopy(m.get("stock")),
                                {"list": matched[1][0], "entry": matched[1][1]}) for m in members]
    changes.append(edit.Change(uid, ("fields", "trigger"), f.get("trigger"), prefabs.TRIGGER_AT_ONCE))
    region = f.get("region")
    corners = [o for o in level.objects["objects"] if key(o) == "regionpoint"
               and o.get("fields", {}).get("region") == region]
    corners.sort(key=lambda o: o["fields"].get("corner", 0))
    rewound = len(corners) >= 3 and prefabs.winding([(o["pos"][0], o["pos"][2]) for o in corners]) < 0
    if rewound:  # corner 0 stays, the rest go the other way round
        changes += [edit.Change(o["uid"], ("fields", "corner"), k, (len(corners) - k) % len(corners))
                    for k, o in enumerate(corners)]
    return changes, {"squadron": uid, "stock": changes[0].new,
                     "members": len(members) if matched[1] is not None else 0, "regionRewound": rewound}


def patrol(level, sq: dict, squads: dict, key, catalog=None) -> list[edit.Change]:
    """The changes that put a squadron in patrol mode: kind's low 4 bits 6 (the others kept; from its stock entry in
    `catalog` when the kind isn't set), the game's own patrol squad of its enemy copied when there is one (fix(): its
    other bytes fit), and behaviour 2 (walk the nodes; it sets off from the node nearest to it) that fights like a
    smart squad once it sees the hero (alert and hurt behaviour 0x11). Play-tested: never give a patrol point of it a
    start value (it rides a lift and stops for good)."""
    f = sq.get("fields", {})
    kind = f.get("kind")
    if kind is None and sq.get("stock") and catalog is not None:
        kind = catalog.stock_entry(sq["stock"]["list"], sq["stock"]["entry"])[0x24]
    uid = sq["uid"]
    changes = [edit.Change(uid, ("fields", "kind"), f.get("kind"), ((kind or 0) & 0xF0) | PATROL_MODE)]
    if examples(squads, f.get("baddyType", -1), PATROL_MODE) is not None:
        changes += fix(level, sq, squads, key, PATROL_MODE)[0]
    for name, value in (("behaviour", PATROL_BEHAVIOUR), ("alertBehaviour", SMART), ("hurtBehaviour", SMART)):
        changes.append(edit.Change(uid, ("fields", name), f.get(name), value))
    return changes


def near_patrol(level, sq: dict, key) -> bool:
    """Whether a patrol point of the level is within PATROL_NEAR of the squadron (seen from above)."""
    return any(key(o) == "patrolpoint" and math.dist(o["pos"][::2], sq["pos"][::2]) <= PATROL_NEAR
               for o in level.objects["objects"])
