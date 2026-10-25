"""The local catalog (task S3): what Studio knows about the user's ROM, decoded once into the user's data folder.

    <data>/catalog/<sha1 prefix>/levels.json      every level: name, world, track, sky, object lists, music
    <data>/catalog/<sha1 prefix>/objtypes.json    every object list id: its definition's name, behaviour, models,
                                                  scale, and where stock lists use it
    <data>/catalog/<sha1 prefix>/map.json         the planet map (points, discs, routes, entry levels)
    <data>/catalog/<sha1 prefix>/tracks/NNN.glb   a track's geometry, exported on first use
    <data>/catalog/<sha1 prefix>/models/XXXX.glb  a model (rest pose), exported on first use

All of it is derived from the user's ROM and stays on the user's machine: never in a project, a pack or the repo.
"""

import json
import struct
from pathlib import Path

from jfgstudio import formats, rom, settings

OBJINDEX_ASSET, OBJDEF_TABLE, OBJDEF_DATA = 0x30, 0x2E, 0x2F
CATALOG_FORMAT = 3  # the catalog is rebuilt when objtypes.json is older
# header fields the Level window offers stock values of (music, sky, fog, ambient loops ...)
LEVEL_PROPS = ("tune", "tuneChannelMask", "voiceLimit", "ambientSounds", "fogNear", "fogFar", "fog3", "fogR", "fogG",
               "fogB", "clearR", "clearG", "clearB", "weather", "fov", "nightVision", "dayLength", "lights",
               "underWaterLights", "regionIndex")
EXAMPLES = 8  # stock entries kept per object type, for the Prefabs window's "start from" list
ASSET_KINDS = {0: "models", 1: "sprites", 4: "textures"}


def object_types(assets) -> dict[int, dict]:
    """List object id -> {"name", "behaviour", "def", "scale", "kind", "assets"} from the object index (0x30) and the
    definitions (0x2E/0x2F), MSR D9: +0x00 f32 scale, +0x04 name (16 bytes), +0x1C s16 behaviour, +0x1E what +0x30
    lists (0 models, 1 sprites, 4 textures), +0x1F how many, +0x30 the offset of their s32 ids (models: one per LOD,
    bit 0x8000 = simple instance)."""
    jf = formats.load()
    index = assets.section(OBJINDEX_ASSET)
    objindex = struct.unpack(f">{len(index) // 2}h", index[:len(index) // 2 * 2])
    table, count = jf.read_table(assets.section(OBJDEF_TABLE))
    data = assets.section(OBJDEF_DATA)
    out = {}
    for oid, d in enumerate(objindex):
        if 0 <= d < count:
            raw = data[table[d]:table[d + 1]]
            if len(raw) < 0x34:
                continue
            kind, n = struct.unpack_from(">bb", raw, 0x1E)
            at = struct.unpack_from(">i", raw, 0x30)[0]
            ids = ([struct.unpack_from(">i", raw, at + 4 * i)[0] & 0x7FFF for i in range(n)]
                   if n > 0 and 0 <= at and at + 4 * n <= len(raw) else [])
            out[oid] = {"name": raw[4:0x14].split(b"\0")[0].decode("ascii", "replace"),
                        "behaviour": struct.unpack_from(">h", raw, 0x1C)[0], "def": d,
                        "scale": round(struct.unpack_from(">f", raw, 0)[0], 6),
                        "kind": ASSET_KINDS.get(kind, str(kind)), "assets": ids}
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
                           "tune": f["tune"], "region": f["region"],
                           "header": {k: f[k] for k in LEVEL_PROPS if k in f}})
        self._write("levels.json", levels)
        types = object_types(self.assets)
        lists = self.pair(self.levels_mod.OBJLIST_TABLE, self.levels_mod.OBJLIST_DATA)
        for n, raw in enumerate(lists):
            for i, e in enumerate(self.levels_mod.objlist_entries(raw)):
                if e is not None and e[0] in types:
                    t = types[e[0]]
                    t["uses"] = t.get("uses", 0) + 1
                    if len(t.setdefault("examples", [])) < EXAMPLES:
                        t["examples"].append([n, i])
        self._write("objtypes.json", {"format": CATALOG_FORMAT,
                                      "types": {f"0x{k:X}": v for k, v in types.items()}})
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

    def objtypes(self) -> dict[int, dict]:
        """objtypes.json (built when missing or older than this Studio's format), by object id."""
        path = self.dir / "objtypes.json"
        doc = None
        if path.exists():
            with open(path, encoding="utf-8") as f:
                doc = json.load(f)
        if not doc or doc.get("format") != CATALOG_FORMAT:
            self.build(log=lambda _: None)
            with open(path, encoding="utf-8") as f:
                doc = json.load(f)
        return {int(k, 16): v for k, v in doc["types"].items()}

    def definition_models(self, d: int) -> list[int]:
        """The model ids of object definition `d` (by definition index, as a level's sky at header +0x58 names it)."""
        table, count = self.jf.read_table(self.assets.section(OBJDEF_TABLE))
        if not 0 <= d < count:
            return []
        raw = self.assets.section(OBJDEF_DATA)[table[d]:table[d + 1]]
        if len(raw) < 0x34 or raw[0x1E] != 0:
            return []
        n = raw[0x1F]
        at = struct.unpack_from(">i", raw, 0x30)[0]
        if not (0 <= at and at + 4 * n <= len(raw)):
            return []
        return [struct.unpack_from(">i", raw, at + 4 * i)[0] & 0x7FFF for i in range(n)]

    def levels(self) -> list[dict]:
        """levels.json (built with objtypes.json)."""
        self.objtypes()
        with open(self.dir / "levels.json", encoding="utf-8") as f:
            return json.load(f)

    def start_points(self, level_id: int) -> list[int]:
        """The start point numbers a stock level's object lists hold (start point entry +0x0B)."""
        lv = self.levels()[level_id]
        out = set()
        for n in (lv["objectList0"], lv["objectList1"]):
            try:
                entries = self.objlist(n)
            except (IndexError, ValueError):
                continue
            out |= {e[1][0x0B] for e in entries if e and e[0] == 0x0D and len(e[1]) > 0x0B}
        return sorted(out)

    def model_glb(self, model_id: int) -> Path:
        """A model in its rest pose as .glb in the catalog (exported the first time)."""
        from jfgformats import models
        path = self.dir / "models" / f"{model_id:04X}.glb"
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            if not hasattr(self, "_models"):
                self._models = models.ModelData(self.assets)
            tmp = path.with_suffix(".tmp")
            models.export_gltf(self._models, self._models.model(model_id), str(tmp), False)
            tmp.replace(path)
        return path

    def _write(self, name: str, doc) -> None:
        with open(self.dir / name, "w", encoding="utf-8", newline="\n") as f:
            json.dump(doc, f, indent=1, sort_keys=True)
            f.write("\n")
