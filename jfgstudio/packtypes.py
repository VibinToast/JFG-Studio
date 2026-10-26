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
"project"}]; "project" is Studio's only, mod.json gets id:version).
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
    owner = project if r.is_pack else dependency_project(project, r.namespace)
    doc = owner.objtypes.get(r.name)
    if doc is None:
        where = "the pack" if r.is_pack else r.namespace
        raise PackTypeError(f"{where} has no object type {r.name!r} (objtypes: {', '.join(owner.objtypes) or 'none'})")
    return {"name": doc["name"], "base": refs.parse(doc["base"], "objtype").number(),
            "behaviour": doc.get("behaviour"), "model": doc.get("model"), "scale": doc.get("scale", 1.0),
            "copyModel": doc.get("copyModel", False),
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
