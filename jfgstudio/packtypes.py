"""Object types of a pack's own (project objtypes/<name>.json), made from a stock definition by field edits, and those
of a dependency (a library pack such as restored content):

    {"name": "Bomb pad", "base": "stock:objtype/0x171", "behaviour": 76, "model": 379, "scale": 1.0}

The build copies the base's definition ("copyOf") with the behaviour at +0x1C (s16), every model slot's id (the
flag bits of each slot kept) and the scale at +0x00 set, and registers it as "objtypes" {append: objtype_<name>,
def: objdef_<name>, like: base} (decomp MSR "Unused props for the enemy test hub": definition +0x1C behaviour, +0x1F
model count, +0x30 the offset of the u32 model ids). An object of it (pack:objtype/<name>, or <mod id>:objtype/<name>
for a dependency's) is written as its base, its bytes from a stock entry of its behaviour, with a u16 fixup at +0 that
the port fills with the type's id (objtype_<name>, or <mod id>:objtype_<name>).

A dependency's types are read from its project folder, named in project.json ("dependencies": [{"id", "version",
"project"}]; "project" is Studio's only, mod.json gets id:version). Or from a mod that isn't a Studio project (a
hand-made one with code of its own: {"id", "version", "mod": its folder}): its jfg_assets.json "objtypes"
[{"append", "def", "like"}], each placed as <mod id>:objtype/<append without objtype_> and written as its "like" (the
stock object id the squad code treats it as, so its entries are that type's) with the u16 fixup <mod id>:<append>.
"""

from pathlib import Path

from jfgstudio import refs

PLAIN_ENTRY = 0x0A  # an entry of its header alone: for a type with no code (behaviour 0) or one no list places


def animation_count(models, model_id: int) -> int:
    """The animations asset 0x28 lists for a model: the count the game gives it at load (model +0x4E), joints or
    not (jfgformats ModelData.animations lists them only for a model with joints)."""
    import struct
    table, _ = models.anim_lists
    start, end = (v >> 1 for v in struct.unpack(">HH", table[model_id * 2:model_id * 2 + 4]))
    return max(0, end - start)


def crashes_animating(models, model_id: int) -> bool:
    """A model with animations listed but no joints (+0x4F): the game animates any model with an animation count, and
    gen_anim_data crashes on it (decomp: the AntTank's model 0x73, the only one in the US ROM)."""
    return animation_count(models, model_id) > 0 and not models.model(model_id).num_joints


class PackTypeError(ValueError):
    pass


_DEPS: dict[str, tuple[float, object]] = {}  # project.json path -> (mtime, Project): dependencies read once


def dependency_project(project, mod_id: str):
    """The project of a dependency `mod_id`, from its "project" folder in project.json (relative to this project)."""
    from jfgstudio import project as proj
    dep = next((d for d in project.meta.get("dependencies", []) if d["id"] == mod_id), None)
    if dep is None:
        raise PackTypeError(f"{mod_id} is not a dependency of the pack (add_dependency)")
    if not dep.get("project"):
        raise PackTypeError(f"dependency {mod_id}: give its project folder (add_dependency with project) so Studio "
                            "can read its object types")
    path = (Path(project.path) / dep["project"]).resolve()
    meta = path / "project.json"
    if not meta.is_file():
        raise PackTypeError(f"dependency {mod_id}: {path} is not a project")
    stamp = max([meta.stat().st_mtime] + [f.stat().st_mtime for f in (path / "objtypes").glob("*.json")])
    cached = _DEPS.get(str(meta))
    if cached is None or cached[0] != stamp:
        cached = (stamp, proj.load(path))
        _DEPS[str(meta)] = cached
    if cached[1].id != mod_id:
        raise PackTypeError(f"dependency {mod_id}: {path} is the project of {cached[1].id}")
    return cached[1]


_MODS: dict[str, tuple[float, dict]] = {}  # jfg_assets.json path -> (mtime, read_mod's result)
OBJTYPE_PREFIX = "objtype_"


def read_mod(folder) -> dict:
    """{"id", "version", "types": {name: {"append", "def", "like"}}} of a mod folder that isn't a Studio project:
    its mod.toml's [manifest] id and version, and its jfg_assets.json "objtypes" (name: the append name without
    objtype_)."""
    import json
    import tomllib
    folder = Path(folder)
    toml = folder / "mod.toml"
    if not toml.is_file():
        raise PackTypeError(f"{folder}: no mod.toml (a mod folder holds its mod.toml and jfg_assets.json)")
    assets = folder / "jfg_assets.json"
    stamp = max(toml.stat().st_mtime, assets.stat().st_mtime if assets.is_file() else 0)
    cached = _MODS.get(str(assets))
    if cached is not None and cached[0] == stamp:
        return cached[1]
    try:
        manifest = tomllib.loads(toml.read_text(encoding="utf-8")).get("manifest", {})
        doc = json.loads(assets.read_text(encoding="utf-8")) if assets.is_file() else {}
    except (ValueError, OSError) as e:
        raise PackTypeError(f"{folder}: {e}") from None
    if not manifest.get("id"):
        raise PackTypeError(f"{toml}: no [manifest] id")
    types = {}
    for t in doc.get("objtypes", []):
        append = t.get("append", "")
        name = append[len(OBJTYPE_PREFIX):] if append.startswith(OBJTYPE_PREFIX) else append
        if refs._NAME.match(name):
            types[name] = {"append": append, "def": t.get("def"), "like": t.get("like")}
    out = {"id": manifest["id"], "version": manifest.get("version", ""), "types": types}
    _MODS[str(assets)] = (stamp, out)
    return out


def dependency_mod(project, mod_id: str) -> dict:
    """read_mod of a dependency `mod_id` named by its mod folder ("mod" in project.json, relative to this project)."""
    dep = next(d for d in project.meta.get("dependencies", []) if d["id"] == mod_id)
    mod = read_mod((Path(project.path) / dep["mod"]).resolve())
    if mod["id"] != mod_id:
        raise PackTypeError(f"dependency {mod_id}: {dep['mod']} is the mod {mod['id']}")
    return mod


def dependency_types(project, mod_id: str) -> dict:
    """{ref: description} of the object types a dependency gives (a project's, or a mod folder's)."""
    dep = next((d for d in project.meta.get("dependencies", []) if d["id"] == mod_id), None)
    if dep is not None and dep.get("mod"):
        return {f"{mod_id}:objtype/{n}": (f"like stock 0x{t['like']:X}" if t["like"] is not None
                                          else "no like: not placeable")
                for n, t in dependency_mod(project, mod_id)["types"].items()}
    return {f"{mod_id}:objtype/{n}": t["name"] for n, t in dependency_project(project, mod_id).objtypes.items()}


def is_pack_type(type_ref: str) -> bool:
    """An object type of the pack's own or of a dependency (not stock, not a kind)."""
    try:
        r = refs.parse(type_ref)
    except refs.RefError:
        return False
    return r.category == "objtype" and not r.is_stock


def resolve(project, type_ref: str) -> dict:
    """{"name", "base" (stock object id), "behaviour" (None: the base's), "model" (None: the base's), "scale",
    "fixup" (the port's name for its id)} of pack:objtype/<name> or <mod id>:objtype/<name>."""
    r = refs.parse(type_ref, "objtype")
    if r.is_stock:
        raise PackTypeError(f"{type_ref} is a stock type")
    dep = None if r.is_pack else next((d for d in project.meta.get("dependencies", []) if d["id"] == r.namespace),
                                      None)
    if dep is not None and dep.get("mod"):
        t = dependency_mod(project, r.namespace)["types"].get(r.name)
        if t is None:
            raise PackTypeError(f"the mod {r.namespace} has no object type {r.name!r} (its jfg_assets.json objtypes: "
                                f"{', '.join(dependency_mod(project, r.namespace)['types']) or 'none'})")
        if not isinstance(t["like"], int):
            raise PackTypeError(f"{type_ref}: its objtypes entry has no \"like\" (the stock object id its entries "
                                "are like), so Studio can't tell how to write it")
        return {"name": t["append"], "base": t["like"], "behaviour": None, "model": None, "scale": 1.0,
                "copyModel": False, "rotate": 0, "fixup": f"{r.namespace}:{t['append']}"}
    owner = project if r.is_pack else dependency_project(project, r.namespace)
    doc = owner.objtypes.get(r.name)
    if doc is None:
        where = "the pack" if r.is_pack else r.namespace
        raise PackTypeError(f"{where} has no object type {r.name!r} (objtypes: {', '.join(owner.objtypes) or 'none'})")
    return {"name": doc["name"], "base": refs.parse(doc["base"], "objtype").number(),
            "behaviour": doc.get("behaviour"), "model": doc.get("model"), "scale": doc.get("scale", 1.0),
            "copyModel": doc.get("copyModel", False), "rotate": doc.get("rotate", 0),
            "fixup": f"objtype_{r.name}" if r.is_pack else f"{r.namespace}:objtype_{r.name}"}


def example(types: dict, base: int, behaviour: int | None) -> list | None:
    """A stock entry [list, entry] to start an object of a type made from `base` with `behaviour`: the base's own
    when the behaviour is the base's, else the most placed type's of that behaviour (its code reads the entry)."""
    t = types.get(base, {})
    if behaviour is None or behaviour == t.get("behaviour"):
        return (t.get("examples") or [None])[0]
    same = [v for v in types.values() if v.get("behaviour") == behaviour and v.get("examples")]
    if not same:
        return None
    return max(same, key=lambda v: v.get("uses", 0))["examples"][0]


# ---- a mod folder's own models, for the editor's Scene

class _ModModel:
    """What jfgformats' export_gltf reads of a ModelData (texture_png; animations are skipped with fps=0), for one
    model of a mod's pack: its unpacked bytes, with its texture ids set to 0x8000 | k for its k-th own texture."""

    def __init__(self, jf_models, data: bytes, textures: list[bytes]):
        self.m, self.data, self.textures = jf_models, data, textures
        self.count, self.tex_count = 1, len(textures)
        self.png_cache = {}

    def model(self, model_id: int):
        model = self.m.Model(model_id, self.data)
        model.file = self.m.read_model_file(self.data)
        return model

    def animations(self, model):
        return []

    def _texture(self, tex_id: int) -> bytes:
        return self.m.unpack_texture_entry(self.textures[min(tex_id & 0x7FFF, len(self.textures) - 1)])[1]

    def texture_frames(self, tex_id: int):
        """As ModelData.texture_frames: (frames, frame delay, blends with the next frame, byte 0x18)."""
        import struct
        data = self._texture(tex_id)
        flags, = struct.unpack(">h", data[6:8])
        delay, = struct.unpack(">H", data[0x14:0x16])
        return self.m.Texture(data).frames, delay, bool(flags & 0x40) and data[0x1B] < 2, data[0x18]

    def texture_wraps(self, tex_id: int):
        data = self._texture(tex_id)
        return not data[0x1C] & 2 and data[0x1D] != 0, not data[0x1E] & 2 and data[0x1F] != 0

    def texture_png(self, tex_id: int, frame: int = 0):
        key = (tex_id, frame)
        if key not in self.png_cache:
            m = self.m
            data = self._texture(tex_id)
            tex = m.Texture(data)
            offset = tex.frame_offset(min(frame, tex.frames - 1))
            pixels = m.decode(tex.format, tex.width, tex.height, data[offset:offset + tex.base_size])
            mode = m.PNG_MODES[tex.format]
            self.png_cache[key] = (m.write_png(mode, tex.width, tex.height, pixels), m.alpha_kind(mode, pixels),
                                   tex.width, tex.height)
        return self.png_cache[key]


def mod_model(project, type_ref: str, jf, cache_dir) -> tuple[str, float] | None:
    """(.glb path, scale) of the model a mod folder's type shows (its definition's model fixup at +0xE0 and scale
    set at +0x00 in its jfg_assets.json, the model and textures from the pack's own files), exported into cache_dir
    the first time and again when the pack's files change; None for a type that isn't a mod folder's, or a pack the
    editor can't read (it then draws the type's like enemy). jf: the jfgformats module."""
    import hashlib
    import importlib
    import json
    import struct
    try:
        jf_models = importlib.import_module(f"{jf.__name__}.models")
    except ImportError:
        return None
    try:
        r = refs.parse(type_ref, "objtype")
    except refs.RefError:
        return None
    dep = None if r.is_pack or r.is_stock else next(
        (d for d in project.meta.get("dependencies", []) if d["id"] == r.namespace), None)
    if dep is None or not dep.get("mod"):
        return None
    folder = (Path(project.path) / dep["mod"]).resolve()
    try:
        t = dependency_mod(project, r.namespace)["types"].get(r.name)
        doc = json.loads((folder / "jfg_assets.json").read_text(encoding="utf-8"))
        entries = {e["append"]: e for e in doc.get("entries", []) if "append" in e}
        objdef = entries[t["def"]]
        model_ref = next(f["ref"] for f in objdef.get("fixups", []) if f.get("at") == 0xE0)
        entry = entries[model_ref.split(":", 1)[-1]]
        scale = 1.0
        for st in objdef.get("set", []):
            if st.get("at") == 0 and len(st.get("hex", "")) >= 8:
                scale = struct.unpack(">f", bytes.fromhex(st["hex"][:8]))[0]
        tex_refs = []
        for f in entry.get("fixups", []):
            name = f["ref"].split(":", 1)[-1]
            if name not in tex_refs:
                tex_refs.append(name)
        files = [folder / entry["file"]] + [folder / entries[n]["file"] for n in tex_refs]
        stamp = hashlib.sha1("|".join(f"{f}:{f.stat().st_mtime_ns}" for f in files).encode()).hexdigest()[:10]
        out = Path(cache_dir) / f"{r.namespace}_{r.name}_{stamp}.glb"
        if not out.exists():
            raw = files[0].read_bytes()
            data = bytearray(jf_models.unpack(raw) if entry.get("packed") else raw)
            for f in entry.get("fixups", []):
                value = tex_refs.index(f["ref"].split(":", 1)[-1]) + f.get("add", 0)
                fmt = {"u16": ">H", "s16": ">h", "u8": ">B", "u32": ">I"}[f.get("type", "u16")]
                struct.pack_into(fmt, data, f["at"], value)
            mm = _ModModel(jf_models, bytes(data), [f.read_bytes() for f in files[1:]])
            out.parent.mkdir(parents=True, exist_ok=True)
            tmp = out.with_suffix(".tmp")
            jf_models.export_gltf(mm, mm.model(0), str(tmp), False, fps=0)
            tmp.replace(out)
        return str(out), scale
    except Exception:  # noqa: BLE001 (a pack the editor can't read: its type is drawn as its like enemy)
        return None
