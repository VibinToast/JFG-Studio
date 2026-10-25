"""The pack compiler (task S6): a project -> an `.nrm` the port loads.

How (phase 0): the project's content is written into a copy of the user's ROM with jfgformats (tracks and their new
textures, level headers from the neutral template, object lists), the same way the decomp's tools build test ROMs.
Then the copy is compared with the original, entry by entry, and only the entries that changed or were added go into
the pack (`jfg_assets.json`: "entry" to replace, "append" with "expect" to add). The planet map, the world name and
Juno's new-game worlds are not asset data: they go into `patch.bps`, built against a separate copy that has only those
changes, so the patch carries only new bytes.

Phase 0 shortcuts (Docs/PHASE0_TASKS.md): fixed ids (`fixedId`, `fixedObjectLists`, `fixedWorld`) instead of the
content linker, and the map as a patch (one patch mod at a time) instead of the planet registry.
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
        from jfgformats import levels, mapdata, models, tracks
        self.lv, self.mapdata, self.models, self.tracks = levels, mapdata, models, tracks
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
    def write_assets(self, r) -> None:
        data = self.models.ModelData(self.catalog.assets)
        results = {}
        for name, tid in self.track_ids.items():
            glb = self.p.geometry_path(name)
            self.log(f"track {name}: importing {glb.name} as track {tid}")
            results[tid] = self.tracks.import_track(data, str(glb), track_id=tid, log=self.log)
        if results:
            self.tracks.build(r, data, results, log=self.log)
        headers, lists = {}, {}
        for name, level in self.p.levels.items():
            entries = [self.entry(o, name) for o in level.objects["objects"]]
            slots = self.level_lists[name]
            lists[slots["slot1"]] = _list_raw(entries)
            lists[slots["slot0"]] = _list_raw([])
            track = refs.parse(level.level["track"])
            fields = dict(level.level["header"])
            fields.update({"track": track.number() if track.is_stock else self.track_ids[track.name],
                           "track2": level.level.get("sky", -1),
                           "objectList0": slots["slot0"], "objectList1": slots["slot1"]})
            if level.level.get("ambient"):
                fields["ambientSounds"] = (list(level.level["ambient"]) + [-1, -1, -1])[:3]
            try:
                headers[self.level_ids[name]] = self.lv.new_header(level.level["name"], **fields)
            except self.jf.FormatError as e:
                raise BuildError([f"levels/{name}/level.json: {e}"]) from e
        for line in self.lv.build(r, headers=headers, lists=lists):
            self.log(line)

    def diff(self, r) -> list[tuple[int, int, str, int, bytes, bool]]:
        """(table, data, what, index, entry bytes, appended) for every entry that differs from the user's ROM."""
        out = []
        for table, data, what, how in SETS:
            before = self.jf.pair_entries(self.catalog.assets.section(table), self.catalog.assets.section(data))
            after = r.entries(table, data, self.jf.read_table(r.section(table))[1])

            for i, e in enumerate(after):
                if i >= len(before):
                    out.append((table, data, what, i, e, True))
                elif self._norm(e, how) != self._norm(before[i], how):
                    out.append((table, data, what, i, e, False))
        return out

    def _norm(self, entry: bytes, how: str) -> bytes:
        """An entry as the game unpacks it, so a stock entry the build only packed again counts as unchanged."""
        if how == "packed":
            return self.jf.unpack(entry)
        if how == "texture":
            return self.jf.unpack_texture_entry(entry)[1]
        return entry

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
    r = b.jf.Rom(image, log=log)
    try:
        b.write_assets(r)
        changes = b.diff(r)
        patched = b.map_image()
    except b.jf.FormatError as e:
        raise BuildError([str(e)]) from e

    result = BuildResult(Path(out))
    names = {("level", n): k for k, n in b.level_ids.items()}
    names.update({("track", n): k for k, n in b.track_ids.items()})
    files, entries = {}, []
    for table, data, what, index, raw, appended in changes:
        fname = f"assets/{what}_{index}.bin"
        files[fname] = raw
        e = {"table": table, "data": data, "file": fname}
        if appended:
            e.update({"append": f"{what}_{names.get((what, index), index)}", "expect": index})
        else:
            e["entry"] = index
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
