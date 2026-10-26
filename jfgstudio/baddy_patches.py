"""Patches of the game's enemy animation tables (project baddies.json; the port's jfg_assets.json "baddies",
applied by jfg_baddies.cpp each time overlay 3 loads, in mod order):

    [{"row": 17, "anim": 16, "expect": {"action": 1}, "set": {"action": 53}}, ...]

`row` is a baddy row (jfgformats.baddies.row_of of a member object id), `anim` an entry of that row's animation set,
and the fields are jfgformats.baddies.decode_anim's. "expect" (the stock values of what it sets, from the player's
ROM) makes the port skip an entry made for other data. The stock rows come from jfgformats.baddies.read.
"""

import copy

FIELDS = {"action": (0, 255), "altAction": (0, 255), "weight": (0, 31), "stance": (0, 7), "fireSet": (0, 126),
          "loops": (0, 15)}  # fireSet may also be None: no fire set


class PatchError(ValueError):
    pass


def check_set(values: dict) -> None:
    """Refuses unknown fields and values out of range."""
    for name, v in values.items():
        if name not in FIELDS:
            raise PatchError(f"unknown field {name!r}: {', '.join(FIELDS)}")
        lo, hi = FIELDS[name]
        if v is None and name == "fireSet":
            continue
        if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
            raise PatchError(f"{name} must be a whole number {lo}-{hi}" + (" or null (none)" if name == "fireSet"
                                                                             else "") + f", not {v!r}")


def applied(rows: list[dict], patches: list[dict]) -> list[dict]:
    """The stock rows (jfgformats.baddies.read()["rows"]) with the patches' "set" values on their entries."""
    out = copy.deepcopy(rows)
    for p in patches:
        anims = out[p["row"]]["anims"]
        if 0 <= p["anim"] < len(anims):
            anims[p["anim"]].update(p["set"])
    return out


def overfull(rows: list[dict], baddies) -> dict[int, dict[int, int]]:
    """{row: {action: entries}} for the sets where more than baddies.PICK_LIST entries answer one action."""
    return {r["row"]: o for r in rows if (o := baddies.overfull_actions(r["anims"]))}


def problems(rows: list[dict], patches: list[dict], baddies) -> list[str]:
    """What the build refuses: an entry past its set, and sets the patches leave overfull."""
    out = []
    for p in patches:
        if not 0 <= p["row"] < len(rows):
            out.append(f"baddies.json: row {p['row']} is not a baddy row (0-{len(rows) - 1})")
        elif not 0 <= p["anim"] < len(rows[p["row"]]["anims"]):
            out.append(f"baddies.json: row {p['row']} has {len(rows[p['row']]['anims'])} animations; there is no "
                       f"anim {p['anim']}")
    if out:
        return out
    for row, acts in sorted(overfull(applied(rows, patches), baddies).items()):
        if any(p["row"] == row for p in patches):
            for act, n in sorted(acts.items()):
                out.append(f"baddies.json: row {row} ends with {n} animations answering action {act}; the game's "
                           f"pick list holds {baddies.PICK_LIST} (more crash it)")
    return out
