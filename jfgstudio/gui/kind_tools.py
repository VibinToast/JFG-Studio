"""The Kinds window (task S24): object types made from stock ones. A kind keeps its base's model, animations and
behaviour and replaces the model's textures with the pack's own images (a reskinned enemy, a recoloured crate), at
its own size. The port builds it at load from the player's own data, so the pack carries only the new images.

Export base textures writes the base model's textures as PNG into the kind's folder to paint over: they come from the
player's ROM, so they stay in the project folder on this machine and are not for sharing as they are.
"""

from pathlib import Path

from imgui_bundle import hello_imgui, imgui

from jfgstudio import refs
from jfgstudio.gui import theme


def _app():
    from jfgstudio.gui import app
    return app


def base_oid(o: dict) -> int | None:
    """The stock object id an object is drawn and placed as: its own, or its kind's base."""
    S = _app().S
    t = o.get("type", "")
    if t.startswith("pack:kind/") and S.project:
        kind = S.project.kinds.get(t.split("/", 1)[1])
        if kind:
            return refs.parse(kind["base"], "objtype").number()
        return None
    if t.startswith("stock:objtype/"):
        try:
            return int(t.split("/", 1)[1], 0)
        except ValueError:
            return None
    from jfgstudio import packtypes
    if packtypes.is_pack_type(t) and S.project:  # the pack's own type, or a dependency's: its base
        try:
            return packtypes.resolve(S.project, t)["base"]
        except packtypes.PackTypeError:
            return None
    return None


def pack_model(o: dict) -> int | None:
    """The model an object of the pack's own type (or a dependency's) shows in place of its base's, if it sets one."""
    from jfgstudio import packtypes
    S = _app().S
    if not packtypes.is_pack_type(o.get("type", "")) or not S.project:
        return None
    try:
        return packtypes.resolve(S.project, o["type"])["model"]
    except packtypes.PackTypeError:
        return None


def model_textures(oid: int) -> list[tuple[int, int, int]]:
    """(width, height, texture id) of each texture slot of a stock type's model."""
    S = _app().S
    t = S.objtypes.get(oid)
    if not t or t["kind"] != "models" or not t["assets"]:
        return []
    key = ("model-textures", t["assets"][0])
    if key not in S.model_paths:
        from jfgformats import models
        S.model_paths[key] = models.ModelData(S.cat.assets).model(t["assets"][0]).textures
    return S.model_paths[key]


def kinds_window() -> None:
    A = _app()
    S = A.S
    if not S.project:
        A._muted("Open a project.")
        return
    if S.catalog() is None:
        A._muted(f"No catalog: {S.cat_error}")
        return
    kinds = S.project.kinds
    if imgui.button("New kind"):
        S.kind_pick["open"] = True
    _new_kind_popup()
    imgui.same_line()
    if kinds:
        imgui.set_next_item_width(hello_imgui.em_size(14))
        if imgui.begin_combo("##kind", kinds.get(S.kind, {}).get("name", "")):
            for name, k in kinds.items():
                if imgui.selectable(f"{k['name']}  ({name})", name == S.kind)[0]:
                    S.kind = name
            imgui.end_combo()
    if S.kind not in kinds:
        S.kind = next(iter(kinds), None)
    if S.kind is None:
        A._muted("A kind is an object type made from a stock one with your own textures: New kind starts one. "
                 "Place kinds from the Prefabs window (Pack kinds).")
        return
    k = kinds[S.kind]
    oid = refs.parse(k["base"], "objtype").number()
    A._title(k["name"])
    A._muted(f"pack:kind/{S.kind}, made from {S.type_name(oid)} (0x{oid:X})")
    changed, name = imgui.input_text("Name", k["name"], imgui.InputTextFlags_.enter_returns_true)
    if changed and name.strip():
        k["name"] = name.strip()
        S.planets_dirty = True
    changed, scale = imgui.slider_float("Size", float(k.get("scale", 1.0)), 0.25, 3.0, "%.2f x")
    if changed:
        k["scale"] = round(scale, 2)
        S.planets_dirty = True
    if imgui.is_item_hovered():
        theme.tip("Times the base's size. A squad member is drawn at its squadron's size (the squadron's "
                          "Size field), so for those the build multiplies the squadrons that use this kind")
    imgui.separator_text("Texture slots of the base model")
    if imgui.button("Export base textures (to paint over)"):
        export_base_textures(S.kind, oid)
    if imgui.is_item_hovered():
        theme.tip("Writes the base model's textures as PNG into kinds/" + S.kind + "/ (from your ROM: for "
                          "your own editing, not for sharing as they are)")
    textures = k.setdefault("textures", {})
    for slot, (w, h, _) in enumerate(model_textures(oid)):
        path = S.project.kind_texture(S.kind, slot)
        used = str(slot) in textures
        state = "replaced" if used and path.exists() else "missing file" if used else "the base's"
        colour = theme.colour("ACCENT" if used and path.exists() else "ERROR" if used else "MUTED")
        imgui.text_colored(colour, f"slot {slot}: {w} x {h}  ({state})")
        imgui.same_line()
        if imgui.small_button(f"Use file##{slot}"):
            if path.exists():
                textures[str(slot)] = path.name
                S.planets_dirty = True
            else:
                S.message = f"kinds/{S.kind}/{path.name} is not there yet (export, paint, save it there)"
        imgui.same_line()
        if used and imgui.small_button(f"Base##{slot}"):
            textures.pop(str(slot), None)
            S.planets_dirty = True
    S.validate()


def export_base_textures(name: str, oid: int) -> None:
    S = _app().S
    from jfgformats import models, png
    data = models.ModelData(S.cat.assets)
    out = 0
    for slot, (_w, _h, tex_id) in enumerate(model_textures(oid)):
        path = S.project.kind_texture(name, slot)
        if path.exists():
            continue  # never over a painted one
        try:
            rgba = data.texture_png(tex_id & 0x7FFF)
        except Exception:  # noqa: BLE001 (a texture jfgformats can't read: left out)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(rgba if isinstance(rgba, bytes | bytearray) else png.write_png(*rgba))
        out += 1
    S.message = f"{out} base textures written to kinds/{name}/ (paint them, then Use file)"


def _new_kind_popup() -> None:
    S = _app().S
    d = S.kind_pick
    if d["open"]:
        imgui.open_popup("New kind")
        d["open"] = False
    if not imgui.begin_popup_modal("New kind", None, imgui.WindowFlags_.always_auto_resize)[0]:
        return
    _, d["name"] = imgui.input_text("Name", d["name"])
    _, d["filter"] = imgui.input_text_with_hint("##kfilter", "Search stock types", d["filter"])
    imgui.begin_child("kindbase", imgui.ImVec2(hello_imgui.em_size(26), hello_imgui.em_size(14)))
    for oid, t in sorted(S.objtypes.items(), key=lambda kv: kv[1]["name"].lower()):
        if t["kind"] != "models" or not t.get("uses"):
            continue
        label = f"{t['name']}  0x{oid:X}"
        if d["filter"] and d["filter"].lower() not in label.lower():
            continue
        if imgui.selectable(label, d["base"] == oid)[0]:
            d["base"] = oid
    imgui.end_child()
    if imgui.button("Create") and d["name"].strip() and d["base"] is not None:
        key = "".join(ch if ch.isalnum() else "_" for ch in d["name"].strip().lower()).strip("_") or "kind"
        while key in S.project.kinds:
            key += "_2"
        S.project.kinds[key] = {"format": 1, "name": d["name"].strip(), "base": f"stock:objtype/0x{d['base']:X}",
                                "scale": 1.0, "textures": {}}
        Path(S.project.path / "kinds" / key).mkdir(parents=True, exist_ok=True)
        S.kind = key
        S.planets_dirty = True
        imgui.close_current_popup()
    imgui.same_line()
    if imgui.button("Cancel"):
        imgui.close_current_popup()
    imgui.end_popup()
