"""The local catalog (task S3): what Studio knows about the user's ROM, decoded once into the user's data folder.

    <data>/catalog/<sha1 prefix>/levels.json      every level: name, world, track, sky, object lists, music
    <data>/catalog/<sha1 prefix>/objtypes.json    every object list id: its definition's name and behaviour
    <data>/catalog/<sha1 prefix>/map.json         the planet map (points, discs, routes, entry levels)
    <data>/catalog/<sha1 prefix>/tracks/NNN.glb   a track's geometry, exported on first use

All of it is derived from the user's ROM and stays on the user's machine: never in a project, a pack or the repo.
"""

import json
import struct
from pathlib import Path

from jfgstudio import formats, rom, settings

OBJINDEX_ASSET, OBJDEF_TABLE, OBJDEF_DATA = 0x30, 0x2E, 0x2F


def object_types(assets) -> dict[int, dict]:
    """List object id -> {"name", "behaviour", "def"} from the object index (0x30) and definitions (0x2E/0x2F):
    a definition's name is at +0x04 (16 bytes), its behaviour id the s16 at +0x1C."""
    jf = formats.load()
    index = assets.section(OBJINDEX_ASSET)
    objindex = struct.unpack(f">{len(index) // 2}h", index[:len(index) // 2 * 2])
    table, count = jf.read_table(assets.section(OBJDEF_TABLE))
    data = assets.section(OBJDEF_DATA)
    out = {}
    for oid, d in enumerate(objindex):
        if 0 <= d < count:
            raw = data[table[d]:table[d + 1]]
            if len(raw) >= 0x1E:
                out[oid] = {"name": raw[4:0x14].split(b"\0")[0].decode("ascii", "replace"),
                            "behaviour": struct.unpack(">h", raw[0x1C:0x1E])[0], "def": d}
    return out


class Catalog:
    def __init__(self, image: bytes):
        self.jf = formats.load()
        from jfgformats import levels, mapdata
        self.levels_mod, self.mapdata = levels, mapdata
        self.image = image
        self.assets = self.jf.Assets.from_rom(image)
        self.dir = settings.data_dir() / "catalog" / self.jf.sha1(image)[:12]

    @classmethod
    def open(cls, rom_path: str | None = None) -> "Catalog":
        return cls(rom.load(rom_path))

    # decoded on demand, straight from the ROM
    def pair(self, table: int, data: int) -> list[bytes]:
        return self.jf.pair_entries(self.assets.section(table), self.assets.section(data))

    def objlist(self, n: int) -> list[tuple[int, bytes]]:
        lists = self.pair(self.levels_mod.OBJLIST_TABLE, self.levels_mod.OBJLIST_DATA)
        if not 0 <= n < len(lists):
            raise IndexError(f"there is no object list {n} (0-{len(lists) - 1})")
        return self.levels_mod.objlist_entries(lists[n])

    def stock_entry(self, list_id: int, index: int) -> bytes:
        entries = self.objlist(list_id)
        if not 0 <= index < len(entries) or entries[index] is None:
            raise IndexError(f"object list {list_id} has no entry {index}")
        return entries[index][1]

    def level_count(self) -> int:
        return len(self.pair(self.levels_mod.HEADER_TABLE, self.levels_mod.HEADER_DATA))

    def track_count(self) -> int:
        from jfgformats import tracks
        return tracks.TrackData(self.assets).count

    # written to the data folder
    def build(self, log=print) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        headers = self.pair(self.levels_mod.HEADER_TABLE, self.levels_mod.HEADER_DATA)
        levels = []
        for i, raw in enumerate(headers):
            doc = self.levels_mod.decode_header(raw)
            f = doc["fields"]
            levels.append({"id": i, "name": doc["name"], "world": f["world"], "track": f["track"],
                           "sky": f["track2"], "objectList0": f["objectList0"], "objectList1": f["objectList1"],
                           "tune": f["tune"], "region": f["region"]})
        self._write("levels.json", levels)
        self._write("objtypes.json", {f"0x{k:X}": v for k, v in object_types(self.assets).items()})
        self._write("map.json", self.mapdata.decode_map(bytearray(self.image)))
        log(f"catalog: {len(levels)} levels, map, object types -> {self.dir}")
        return self.dir

    def track_glb(self, track_id: int) -> Path:
        """A stock track as .glb in the catalog (exported the first time)."""
        from jfgformats import models, tracks
        path = self.dir / "tracks" / f"{track_id:03d}.glb"
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            data = models.ModelData(self.assets)
            tracks.export_track(data, tracks.read_track(tracks.TrackData(self.assets).track(track_id)), track_id,
                                str(path))
        return path

    def _write(self, name: str, doc) -> None:
        with open(self.dir / name, "w", encoding="utf-8", newline="\n") as f:
            json.dump(doc, f, indent=1, sort_keys=True)
            f.write("\n")
