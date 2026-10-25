"""The pack compiler: a project -> an `.nrm` the port loads.

Every entry is built on its own with jfgformats' item writers: level headers (`levels.new_header`), object lists,
tracks (`tracks.import_track`, then `set_texture_ids`, `check_track_buffer`, `pack_track`) and their new textures
(`textures.pack_new_texture`). They go into `jfg_assets.json`: "entry" to replace a stock entry, "append" with the
number it will get ("expect") to add one. Packs never carry ROM-built bytes or ROM-end offsets: the decomp's ROM tools
are for emulators only (owner's decision, 2026-10-25). The user's ROM is only read: stock entries an object copies, the
table sizes, and the planet map.

The planet map is not asset data: it goes into `patch.bps`, an in-place edit of overlay 9's data (see map_image).

Still phase 0 style until the port's content linker exists: fixed ids (`fixedId`, `fixedObjectLists`, `fixedWorld`).
"""

import io
import json
import struct
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from jfgstudio import bps, formats, refs, rom
from jfgstudio import objects as obj
from jfgstudio import validate as val
from jfgstudio.catalog import Catalog
from jfgstudio.project import Project

GAME_ID = "jet-force-gemini"
DEFAULT_MIN_RECOMP = "1.1.8"
HEROES = ("vela", "juno", "lupus")
# (table asset, data asset, what, how to compare two entries)
SETS = (
    (0x1E, 0x1F, "level", "raw"),
    (0x1C, 0x1D, "objlist", "raw"),
    (0x24, 0x25, "track", "packed"),
    (0x01, 0x00, "texture", "texture"),
)


class BuildError(Exception):
    def __init__(self, problems: list[str]):
        super().__init__("\n".join(problems))
        self.problems = problems


@dataclass
class BuildResult:
    path: Path
    entries: list[dict] = field(default_factory=list)
    patch_size: int = 0
    patch_literal: int = 0
    log: list[str] = field(default_factory=list)


def _list_raw(entries: list[bytes]) -> bytes:
    body = b"".join(entries)
    raw = struct.pack(">i", len(body)) + bytes(12) + body
    return raw + bytes(-len(raw) % 16)


class _Builder:
    def __init__(self, project: Project, image: bytes, log):
        self.p = project
        self.image = image
        self.log = log
        self.jf = formats.load()
        from jfgformats import levels, mapdata, models, textures, tracks
        self.lv, self.mapdata, self.models, self.tracks = levels, mapdata, models, tracks
        self.textures = textures
        self.catalog = Catalog(image)
        self.schemas = obj.load_schemas()
        self.problems: list[str] = []

    # ids (phase 0: fixed in the project)
    def plan_ids(self):
        self.level_ids, self.level_lists = {}, {}
        for name, level in self.p.levels.items():
            if "fixedId" not in level.level:
                self.problems.append(f"levels/{name}: phase 0 needs \"fixedId\" (the content linker comes later)")
            if "fixedObjectLists" not in level.level:
                self.problems.append(f"levels/{name}: phase 0 needs \"fixedObjectLists\" (slot0, slot1)")
            self.level_ids[name] = level.level.get("fixedId")
            self.level_lists[name] = level.level.get("fixedObjectLists", {})
        count = self.catalog.track_count()
        names = sorted({refs.parse(lvl.level["track"]).name for lvl in self.p.levels.values()
                        if refs.parse(lvl.level["track"]).is_pack})
        self.track_ids = {n: count + i for i, n in enumerate(names)}

    def level_number(self, ref: str) -> int:
        r = refs.parse(ref, "level")
        return r.number() if r.is_stock else self.level_ids[r.name]

    # objects
    def entry(self, o: dict, level_name: str) -> bytes:
        t = refs.parse(o["type"])
        if not t.is_stock or t.category != "objtype":
            raise BuildError([f"levels/{level_name} [{o['uid']}]: {o['type']}: only stock object types in phase 0"])
        schema = obj.schema_for(o, self.schemas)
        base = b""
        if "stock" in o:
            base = self.catalog.stock_entry(o["stock"]["list"], o["stock"]["entry"])
        raw = bytes.fromhex(o["params"]) if "params" in o else base[obj.PARAMS_OFFSET:]
        size = o.get("size") or len(base)
        fields = dict(o.get("fields", {}))
        if o.get("target") and schema and schema.key == "exit":
            n = self.level_number(o["target"])
            fields.update({"levelLow": n & 0xFF, "levelHigh": 0xFF if n < 0x100 else n >> 8})
        return obj.encode_entry(t.number(), o["pos"], size, schema, fields, raw)

    # the asset side
    def assets(self) -> list[dict]:
        """Every entry the pack adds or replaces: {table, data, what, index, raw, append (a name, or None)}."""
        out = []
        counts = {what: self.jf.read_table(self.catalog.assets.section(table))[1] for table, _, what, _ in SETS}
        data = self.models.ModelData(self.catalog.assets)
        next_texture = counts["texture"]
        track_ids = {}
        for name, tid in self.track_ids.items():
            glb = self.p.geometry_path(name)
            self.log(f"track {name}: importing {glb.name} as track {tid}")
            result = self.tracks.import_track(data, str(glb), track_id=tid, log=self.log)
            if result.get("textures"):
                raise BuildError([f"geometry/{glb.name}: it changes the pixels of stock textures, which a pack can't "
                                  "carry yet; give those materials their own images"])
            ids = {}
            for key, png in result.get("newTextures", {}).items():
                ids[key] = 0x8000 | next_texture
                out.append({"table": 0x01, "data": 0x00, "what": "texture", "index": next_texture,
                            "raw": self.textures.pack_new_texture(png), "append": f"texture_{name}_{key}"})
                self.log(f"texture {name}/{key}: new, id 0x{ids[key]:04X}")
                next_texture += 1
            result = self.tracks.set_texture_ids(result, ids)
            self.tracks.check_track_buffer(result["data"], name=f"track {name}")
            # The track names its new textures by number (texture entry k at header +0x00, 8 bytes each, a u32 id
            # without bit 15): fixups, so the port writes the numbers the textures really get.
            textures_at = struct.unpack_from(">I", result["data"], 0)[0]
            fixups = [{"at": textures_at + 8 * int(key), "type": "u32", "ref": f"texture_{name}_{key}"} for key in ids]
            out.append({"table": 0x24, "data": 0x25, "what": "track", "index": tid,
                        "raw": self.tracks.pack_track(result["data"]), "append": f"track_{name}",
                        "fixups": fixups, "packed": True})
            track_ids[name] = tid
        stock_lists = self.catalog.pair(self.lv.OBJLIST_TABLE, self.lv.OBJLIST_DATA)
        known = self.lv.stock_squadrons(stock_lists)
        cutscenes = self.jf.read_table(self.catalog.assets.section(self.lv.CUTSCENE_TABLE))[1]
        for name, level in self.p.levels.items():
            slots = self.level_lists[name]
            entries, list_fixups, at = [], [], 0x10
            for o in level.objects["objects"]:
                entry = self.entry(o, name)
                target = refs.parse(o["target"], "level") if o.get("target") else None
                if target and target.is_pack:
                    # An exit to a pack level: the level's number is the port's to give (lohi = levelLow, levelHigh).
                    list_fixups.append({"at": at + 0x0A, "type": "lohi", "ref": f"level_{target.name}"})
                entries.append(entry)
                at += len(entry)
            lists = {slots["slot1"]: (_list_raw(entries), list_fixups), slots["slot0"]: (_list_raw([]), [])}
            for index, (raw, fixups) in lists.items():
                problems = self.lv.validate_objlist(raw, True, known)
                if problems:
                    raise BuildError([f"levels/{name}/objects.json: {x}" for x in problems])
                e = self._set_entry(0x1C, 0x1D, "objlist", index, raw, counts, f"objlist_{name}_{index}")
                e["fixups"] = fixups
                out.append(e)
            track = refs.parse(level.level["track"])
            fields = dict(level.level["header"])
            fields.update({"track": track.number() if track.is_stock else track_ids[track.name],
                           "track2": level.level.get("sky", -1),
                           "objectList0": slots["slot0"], "objectList1": slots["slot1"]})
            if level.level.get("ambient"):
                fields["ambientSounds"] = (list(level.level["ambient"]) + [-1, -1, -1])[:3]
            try:
                raw = self.lv.new_header(level.level["name"], **fields)
            except self.jf.FormatError as e:
                raise BuildError([f"levels/{name}/level.json: {e}"]) from e
            problems = self.lv.validate_header(raw, cutscenes, len(stock_lists))
            if problems:
                raise BuildError([f"levels/{name}/level.json: {x}" for x in problems])
            e = self._set_entry(0x1E, 0x1F, "level", self.level_ids[name], raw, counts, f"level_{name}")
            if not track.is_stock:
                e["fixups"] = [{"at": 0x54, "type": "s16", "ref": f"track_{track.name}"}]  # header +0x54: the track
            out.append(e)
        # A replacement identical to the stock entry is left out (an empty slot-0 list over an empty stock one).
        stock = {table: self.catalog.pair(table, data) for table, data, _, _ in SETS}
        out = [e for e in out if e["append"] or e["raw"] != stock[e["table"]][e["index"]]]
        # The port numbers appended entries in order, so each set's new ones must follow its last stock one, no gap.
        for _, _, what, _ in SETS:
            new = sorted(e["index"] for e in out if e["what"] == what and e["append"])
            if new and new != list(range(counts[what], counts[what] + len(new))):
                raise BuildError([f"{what}: new entries {new} must follow the last stock one ({counts[what] - 1}) "
                                  "with no gap"])
        return out

    @staticmethod
    def _set_entry(table, data, what, index, raw, counts, name) -> dict:
        return {"table": table, "data": data, "what": what, "index": index, "raw": raw,
                "append": name if index >= counts[what] else None}

    # the patch side
    def map_image(self) -> bytes | None:
        """The planet map, edited in place in a copy of the ROM.

        In the port only in-place edits of overlay 9's data work: the port recompiles every overlay ahead of time from
        the original ROM and finds its code by ROM address, so a grown map (overlay 9 moved to the ROM end, as the
        decomp's `--grow-map` does for emulators) has no code and the game stops when the map opens (2026-10-25).
        Patches to recompiled code (the new-game unlock) do nothing there either. So a phase 0 planet takes over an
        existing map point ("replaces"); extra points and unlocks come with the port's planet registry (P4).
        """
        if not self.p.planets:
            return None
        img = bytearray(self.image)
        doc = self.mapdata.decode_map(img)
        for name, p in sorted(self.p.planets.items()):
            world = p.get("fixedWorld")
            if world is None:
                raise BuildError([f"planets/{name}: phase 0 needs \"fixedWorld\" (15)"])
            if "replaces" not in p:
                raise BuildError([f"planets/{name}: phase 0 needs \"replaces\": a stock map point to take over (an "
                                  "extra map point needs the port's planet registry)"])
            point = refs.parse(p["replaces"], "mappoint").number()
            if not 0 <= point < len(doc["points"]):
                raise BuildError([f"planets/{name}: there is no map point {point}"])
            old_world = doc["points"][point]["world"]
            doc["points"][point]["world"] = world
            row = next((r for r in doc["worldLevels"] if r["world"] == old_world), None)
            if row is None:
                raise BuildError([f"planets/{name}: map point {point}'s world has no entry-level row"])
            row["world"] = world
            row["levels"] = [self.level_number(p["entryLevel"][h]) for h in HEROES]
            self.log(f"planet {name}: takes over map point {point} (world {old_world}), now world {world}")
            # No name string (adding one moves the front-end text, which breaks once the port moves the asset area)
            # and no new-game unlock (a patch to recompiled code): unlock it with an exit's unlockWorld, or the
            # all-levels cheat for testing.
            if p.get("unlock", {}).get("newGame"):
                self.log(f"planet {name}: the new-game unlock does not work in the port yet (planet registry)")
        problems = self.mapdata.validate_map(doc)
        if problems:
            raise BuildError([f"planet map: {x}" for x in problems])
        for line in self.mapdata.encode_map(doc, img, grow=False):
            self.log(line)
        self.jf.fix_crc(img)
        if len(img) != len(self.image):
            raise BuildError(["internal: the map patch grows the ROM; the port only takes in-place map edits"])
        moved = [i for i in range(self.catalog.assets.count)
                 if self.catalog.assets.section(i) != self.jf.Assets.from_rom(bytes(img)).section(i)]
        if moved:
            # The port rebuilds the asset area at a new address when a pack has asset entries, so asset data a
            # patch points elsewhere (offsets relative to the old place) breaks: game data goes in entries only.
            raise BuildError([f"internal: the map patch changes asset data ({moved}); that breaks in the port"])
        return bytes(img)


def build(project: Project, out: str | Path, rom_path: str | None = None, log=print) -> BuildResult:
    problems = [str(p) for p in val.validate(project) if p.severity == val.ERROR]
    if problems:
        raise BuildError(["the project has errors (jfgstudio validate):", *problems])
    image = rom.load(rom_path)
    b = _Builder(project, image, log)
    b.plan_ids()
    if b.problems:
        raise BuildError(b.problems)
    try:
        changes = b.assets()
        patched = b.map_image()
    except b.jf.FormatError as e:
        raise BuildError([str(e)]) from e

    result = BuildResult(Path(out))
    files, entries = {}, []
    for c in changes:
        fname = f"assets/{c['what']}_{c['index']}.bin"
        files[fname] = c["raw"]
        e = {"table": c["table"], "data": c["data"], "file": fname}
        if c["append"]:
            e.update({"append": c["append"], "expect": c["index"]})
        else:
            e["entry"] = c["index"]
        if c.get("fixups"):
            e["fixups"] = c["fixups"]
        if c.get("packed") and c.get("fixups"):
            e["packed"] = True
        entries.append(e)
    result.entries = entries
    meta = project.meta
    manifest = {"id": meta["id"], "version": meta["version"], "display_name": meta["name"],
                "authors": meta.get("authors", []), "description": meta.get("description", ""),
                "short_description": meta.get("description", "")[:120], "game_id": GAME_ID,
                "minimum_recomp_version": meta.get("minimumRecompVersion", DEFAULT_MIN_RECOMP),
                "dependencies": [f"{d['id']}:{d['version']}" for d in meta.get("dependencies", [])]}
    patch = None
    if patched is not None:
        patch = bps.encode(image, patched)
        if bps.apply(image, patch) != patched:
            raise BuildError(["internal: the map patch does not apply"])
        result.patch_size = len(patch)
        result.patch_literal = len(patched) - len(image)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("mod.json", json.dumps(manifest, indent=4, sort_keys=True))
        if entries:
            z.writestr("jfg_assets.json", json.dumps({"entries": entries}, indent=1))
        for name, raw in sorted(files.items()):
            z.writestr(name, raw)
        if patch:
            z.writestr("patch.bps", patch)
    out.write_bytes(buf.getvalue())
    _self_check(b, out)
    log(f"wrote {out}: {len(entries)} entries ({sum(e.get('append') is not None for e in entries)} new)"
        + (f", map patch {len(patch)} bytes" if patch else ""))
    return result


def _self_check(b: _Builder, path: Path) -> None:
    """Reads the pack back and decodes every entry, as the decomp's builds read back their ROM."""
    with zipfile.ZipFile(path) as z:
        json.loads(z.read("mod.json"))
        if "jfg_assets.json" not in z.namelist():
            return
        for e in json.loads(z.read("jfg_assets.json"))["entries"]:
            raw = z.read(e["file"])
            if e["table"] == 0x1E:
                b.lv.decode_header(raw)
            elif e["table"] == 0x1C:
                b.lv.objlist_entries(raw)
            elif e["table"] == 0x24:
                b.tracks.read_track(b.jf.unpack(raw))
            elif e["table"] == 0x01:
                b.jf.unpack_texture_entry(raw)
