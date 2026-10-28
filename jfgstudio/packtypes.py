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
