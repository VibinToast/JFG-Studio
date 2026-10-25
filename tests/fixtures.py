"""Synthetic test projects. Everything here is made up by the tests: no ROM, no game data.

`sample_project` is shaped like the decomp's planet demo (a world-15 planet, a level on a new track, a start point,
an exit, a squadron with two members locking a door, its navigation region), with invented numbers.
"""

import copy
from pathlib import Path

from jfgstudio import project as proj

EXIT, START, SQUADRON, REGION = "stock:objtype/0xB", "stock:objtype/0xD", "stock:objtype/0x4F", "stock:objtype/0x5"


def _objects() -> list[dict]:
    corners = [(-900, -900), (-900, 900), (900, 900), (900, -900)]
    out = [
        {"uid": "start0", "name": "Start", "type": START, "pos": [0, 10, 0], "size": 0x10,
         "fields": {"player": 0, "startPoint": 0, "yaw": 0}},
        {"uid": "exit0", "name": "Exit home", "type": EXIT, "pos": [-600, 0, 0], "size": 0x1C,
         "target": "pack:level/home", "fields": {"startPoint": 0, "radius": 110, "unlockWorld": -1}},
        {"uid": "squad1", "name": "Ant squad", "type": SQUADRON, "pos": [-350, 0, -200], "size": 0x40,
         "fields": {"squadronId": 7, "doorId": 1, "region": 1}},
        {"uid": "ant1", "type": "stock:objtype/0x29", "schema": "squadmember", "pos": [-450, 0, 20], "size": 0x1C,
         "fields": {"squadron": 7}},
        {"uid": "ant2", "type": "stock:objtype/0x29", "schema": "squadmember", "pos": [-610, 0, 320], "size": 0x1C,
         "fields": {"squadron": 7}},
        {"uid": "door1", "type": "stock:objtype/0x100", "schema": "door", "pos": [-520, 0, 0], "size": 0x18,
         "fields": {"doorId": 1}},
    ]
    for k, (x, z) in enumerate(corners):
        out.append({"uid": f"rp{k}", "type": REGION, "pos": [x, 0, z], "size": 0x0C,
                    "fields": {"region": 1, "corner": k}})
    return out


def sample_project(root: Path) -> proj.Project:
    p = proj.create(root / "sample.jfgproj", "test.sample", "Sample planet", "tests")
    gem = proj.new_level(p, "gemini", "Planet Gemini", "pack:track/field")
    gem.level["fixedId"] = 412
    gem.level["sky"] = 180
    gem.level["header"].update({"fogNear": 900, "fogFar": 1000, "fogR": 255, "fogG": 80, "fogB": 200})
    gem.objects["objects"] = _objects()
    home = proj.new_level(p, "home", "Home", "pack:track/field")
    home.objects["objects"] = [
        {"uid": "start0", "type": START, "pos": [0, 0, 0], "size": 0x10, "fields": {"player": 0, "startPoint": 0}},
    ]
    p.planets["gemini"] = {
        "format": 1, "displayName": {"en": "Gemini"}, "fixedWorld": 15,
        "disc": {"index": 8, "x": 60, "y": 320, "radius": 14, "orbitParent": -1},
        "routes": [{"from": "stock:mappoint/2", "dir": "left", "back": "right", "corners": [[130, 320], [78, 320]]}],
        "entryLevel": {"vela": "pack:level/gemini", "juno": "pack:level/gemini", "lupus": "pack:level/gemini"},
        "unlock": {"newGame": ["juno"]},
    }
    p.save()
    p.geometry_path("field").write_bytes(b"glTF")  # placeholder: the validator only checks the file is there
    return p


def clone(p: proj.Project) -> proj.Project:
    return proj.Project(p.path, copy.deepcopy(p.meta), copy.deepcopy(p.levels), copy.deepcopy(p.planets))
