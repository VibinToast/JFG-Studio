"""The Models window (task S22): every model the user's catalog has an object type for, drawn in its rest pose in a
small view of its own (drag to orbit, wheel to zoom), with the object types that use it.

The models are exported from the user's ROM into their data folder on first use, as the Scene does.
"""

import numpy as np
from imgui_bundle import hello_imgui, imgui

from jfgstudio.gui import theme
from jfgstudio.gui import viewport as vp


class ModelView:
    def __init__(self):
        self.view = vp.Viewport()
        self.camera = vp.Camera()
        self.model = None  # model id shown
        self.search = ""
        self.yaw, self.pitch, self.dist = 0.6, -0.35, 400.0
        self.centre = np.zeros(3)


V = ModelView()


def _app():
    from jfgstudio.gui import app
    return app


def model_users() -> dict[int, list[tuple[int, str]]]:
    """Model id -> [(object id, type name)] for every object type whose definition lists models."""
    S = _app().S
    out: dict[int, list] = {}
    for oid, t in S.objtypes.items():
        if t["kind"] == "models":
            for m in dict.fromkeys(t["assets"]):
                out.setdefault(m, []).append((oid, t["name"]))
    return out


def models_window() -> None:
    A = _app()
    S = A.S
    if S.catalog() is None:
        A._muted(f"No catalog: set your ROM and the decomp in File > Settings. {S.cat_error}")
        return
    users = model_users()
    avail = imgui.get_content_region_avail()
    imgui.begin_child("list", imgui.ImVec2(min(hello_imgui.em_size(18), avail.x * 0.4), 0))
    imgui.set_next_item_width(-1)
    _, V.search = imgui.input_text_with_hint("##msearch", "Search models", V.search)
    imgui.begin_child("models")
    q = V.search.lower()
    for mid in sorted(users, key=lambda m: users[m][0][1].lower()):
        names = ", ".join(sorted({n for _, n in users[mid]}))
        if q and q not in names.lower() and q not in f"0x{mid:x}":
            continue
        if imgui.selectable(f"{names}  0x{mid:X}##m{mid}", V.model == mid)[0]:
            V.model = mid
            V.dist = 0.0  # frame it once it is loaded
    imgui.end_child()
    imgui.end_child()
    imgui.same_line()
    imgui.begin_child("view")
    if V.model is None:
        A._muted("Pick a model on the left. Drag to turn it, the wheel to zoom.")
    else:
        _draw(users)
    imgui.end_child()


def _draw(users) -> None:
    S = _app().S
    try:
        path = str(S.cat.model_glb(V.model))
    except Exception as e:  # noqa: BLE001 (a model jfgformats can't export)
        imgui.text_colored(theme.colour("ERROR"), f"model 0x{V.model:X} can't be shown: {e}")
        return
    mesh = V.view.model(path)
    if mesh is None or not mesh.parts:
        imgui.text_colored(theme.colour("ERROR"), f"model 0x{V.model:X} has nothing to draw")
        return
    lo, hi = mesh.bounds
    size = hi - lo
    if V.dist == 0.0:
        V.centre = (lo + hi) / 2
        V.dist = float(max(np.linalg.norm(size), 1.0)) * 1.3
    used = ", ".join(f"{n} 0x{o:X}" for o, n in users.get(V.model, []))
    imgui.text(f"model 0x{V.model:X}: {size[0]:.0f} x {size[1]:.0f} x {size[2]:.0f} units, "
               f"{sum(len(p[0]) for p in mesh.parts) // 3} triangles")
    _app()._muted(f"used by {used}")
    avail = imgui.get_content_region_avail()
    w, h = int(max(avail.x, 8)), int(max(avail.y, 8))
    cam = V.camera
    cam.yaw, cam.pitch = V.yaw, V.pitch
    cam.pos = V.centre - cam.forward() * V.dist
    tex = V.view.render(w, h, cam, np.zeros((0, 7), "f4"), [(path, np.identity(4), (0, 0, 0, 0))])
    if tex is None:
        return
    imgui.image(imgui.ImTextureRef(tex), imgui.ImVec2(w, h), imgui.ImVec2(0, 1), imgui.ImVec2(1, 0))
    if imgui.is_item_hovered():
        io = imgui.get_io()
        if imgui.is_mouse_down(imgui.MouseButton_.left) or imgui.is_mouse_down(imgui.MouseButton_.right):
            V.yaw -= io.mouse_delta.x * 0.01
            V.pitch = float(np.clip(V.pitch - io.mouse_delta.y * 0.01, -1.5, 1.5))
        if io.mouse_wheel:
            V.dist *= 0.85 if io.mouse_wheel > 0 else 1.18
