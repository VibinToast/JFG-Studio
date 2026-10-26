"""The Scene's icons for objects the game draws nothing for (cameras, lights, sounds, exits, triggers ...): a round
badge of a fixed size on screen, as an engine editor draws them, at the top of the object's post. Clicking one picks
the object (app._hit)."""

import math

from imgui_bundle import imgui

# Schema key -> icon; types with no schema by behaviour (the decomp's behaviour table)
BY_KEY = {
    "camerazone": "camera", "cutcamera": "camera", "staticcamera": "camera", "light": "light", "glow": "light",
    "lightbeam": "light", "audio": "sound",
    "exit": "exit", "fogzone": "fog", "texscroll": "scroll", "animtrigger": "trigger", "flagtrigger": "trigger",
    "patrolpoint": "flag", "patrolpath": "flag", "squadron": "squad", "regioninfo": "region", "refuel": "pad",
}
BY_BEHAVIOUR = {17: "camera", 70: "camera", 71: "camera", 100: "camera", 18: "camera", 12: "light", 56: "light",
                132: "light", 127: "light", 33: "trigger", 34: "trigger", 51: "trigger", 105: "sound",
                148: "sound", 11: "fog", 149: "flag", 27: "flag", 136: "region"}
RADIUS = 11.0  # pixels, before the font scale


def kind(key: str | None, behaviour: int | None) -> str:
    return BY_KEY.get(key) or BY_BEHAVIOUR.get(behaviour, "dot")


def radius() -> float:
    return RADIUS * imgui.get_font_size() / 16.0


def draw(dl, x: float, y: float, icon: str, colour, selected: bool, dim: bool) -> None:
    """One badge centred on (x, y): a dark disc ringed in the kind's colour with the kind's glyph."""
    r = radius()
    a = 0.55 if dim and not selected else 1.0
    ring = imgui.get_color_u32(imgui.ImVec4(colour[0], colour[1], colour[2], a))
    back = imgui.get_color_u32(imgui.ImVec4(1.0, 0.85, 0.2, 0.9 * a) if selected else
                               imgui.ImVec4(0.06, 0.09, 0.17, 0.85 * a))
    ink = imgui.get_color_u32(imgui.ImVec4(0.05, 0.05, 0.1, 1.0) if selected else
                              imgui.ImVec4(colour[0], colour[1], colour[2], a))
    c = imgui.ImVec2(x, y)
    dl.add_circle_filled(c, r, back)
    dl.add_circle(c, r, ring, 0, 1.6)
    _GLYPHS.get(icon, _dot)(dl, x, y, r * 0.62, ink)


def _v(x, y):
    return imgui.ImVec2(x, y)


def _dot(dl, x, y, s, ink):
    dl.add_circle_filled(_v(x, y), s * 0.35, ink)


def _camera(dl, x, y, s, ink):
    dl.add_rect_filled(_v(x - s, y - s * 0.55), _v(x + s * 0.35, y + s * 0.55), ink, s * 0.15)
    dl.add_triangle_filled(_v(x + s * 0.35, y), _v(x + s, y - s * 0.55), _v(x + s, y + s * 0.55), ink)
    dl.add_circle_filled(_v(x - s * 0.6, y - s * 0.85), s * 0.3, ink)
    dl.add_circle_filled(_v(x - s * 0.05, y - s * 0.85), s * 0.3, ink)


def _light(dl, x, y, s, ink):
    dl.add_circle_filled(_v(x, y - s * 0.15), s * 0.5, ink)
    dl.add_rect_filled(_v(x - s * 0.25, y + s * 0.35), _v(x + s * 0.25, y + s * 0.7), ink)
    for k in range(5):
        ang = math.pi * (0.95 + 0.275 * k)
        dl.add_line(_v(x + math.cos(ang) * s * 0.7, y - s * 0.15 + math.sin(ang) * s * 0.7),
                    _v(x + math.cos(ang) * s, y - s * 0.15 + math.sin(ang) * s), ink, 1.4)


def _sound(dl, x, y, s, ink):
    dl.add_rect_filled(_v(x - s * 0.9, y - s * 0.3), _v(x - s * 0.45, y + s * 0.3), ink)
    dl.add_quad_filled(_v(x - s * 0.45, y - s * 0.3), _v(x + s * 0.05, y - s * 0.75), _v(x + s * 0.05, y + s * 0.75),
                       _v(x - s * 0.45, y + s * 0.3), ink)
    for rr in (0.45, 0.8):
        dl.path_arc_to(_v(x + s * 0.05, y), s * rr, -0.8, 0.8)
        dl.path_stroke(ink, 1.4)


def _exit(dl, x, y, s, ink):
    dl.add_rect(_v(x - s * 0.6, y - s * 0.85), _v(x + s * 0.2, y + s * 0.85), ink, 0.0, 1.5)
    dl.add_line(_v(x - s * 0.25, y), _v(x + s * 0.95, y), ink, 1.6)
    dl.add_triangle_filled(_v(x + s, y), _v(x + s * 0.55, y - s * 0.4), _v(x + s * 0.55, y + s * 0.4), ink)


def _fog(dl, x, y, s, ink):
    for dx, dy, rr in ((-0.45, 0.15, 0.42), (0.1, -0.15, 0.55), (0.55, 0.2, 0.38)):
        dl.add_circle_filled(_v(x + dx * s, y + dy * s), rr * s, ink)


def _scroll(dl, x, y, s, ink):
    for dx in (-0.45, 0.25):
        dl.add_line(_v(x + dx * s, y - s * 0.6), _v(x + (dx + 0.45) * s, y), ink, 1.8)
        dl.add_line(_v(x + (dx + 0.45) * s, y), _v(x + dx * s, y + s * 0.6), ink, 1.8)


def _trigger(dl, x, y, s, ink):
    pts = [(0.2, -1.0), (-0.55, 0.15), (-0.02, 0.15), (-0.2, 1.0), (0.55, -0.15), (0.02, -0.15)]
    dl.add_triangle_filled(_v(x + pts[0][0] * s, y + pts[0][1] * s), _v(x + pts[1][0] * s, y + pts[1][1] * s),
                           _v(x + pts[2][0] * s, y + pts[2][1] * s), ink)
    dl.add_triangle_filled(_v(x + pts[3][0] * s, y + pts[3][1] * s), _v(x + pts[4][0] * s, y + pts[4][1] * s),
                           _v(x + pts[5][0] * s, y + pts[5][1] * s), ink)
    dl.add_quad_filled(_v(x + pts[1][0] * s, y + pts[1][1] * s), _v(x + pts[0][0] * s, y + pts[0][1] * s),
                       _v(x + pts[4][0] * s, y + pts[4][1] * s), _v(x + pts[3][0] * s, y + pts[3][1] * s), ink)


def _flag(dl, x, y, s, ink):
    dl.add_line(_v(x - s * 0.5, y - s * 0.9), _v(x - s * 0.5, y + s * 0.9), ink, 1.6)
    dl.add_triangle_filled(_v(x - s * 0.5, y - s * 0.9), _v(x + s * 0.8, y - s * 0.45), _v(x - s * 0.5, y), ink)


def _squad(dl, x, y, s, ink):
    for dx in (-0.5, 0.5):
        dl.add_circle_filled(_v(x + dx * s, y - s * 0.35), s * 0.3, ink)
        dl.add_rect_filled(_v(x + (dx - 0.35) * s, y + s * 0.05), _v(x + (dx + 0.35) * s, y + s * 0.75), ink,
                           s * 0.2)


def _region(dl, x, y, s, ink):
    dl.add_quad(_v(x, y - s), _v(x + s, y), _v(x, y + s), _v(x - s, y), ink, 1.6)


def _pad(dl, x, y, s, ink):
    dl.add_rect_filled(_v(x - s * 0.9, y + s * 0.35), _v(x + s * 0.9, y + s * 0.7), ink, s * 0.15)
    dl.add_triangle_filled(_v(x, y - s * 0.8), _v(x - s * 0.5, y), _v(x + s * 0.5, y), ink)


_GLYPHS = {"camera": _camera, "light": _light, "sound": _sound, "exit": _exit, "fog": _fog, "scroll": _scroll,
           "trigger": _trigger, "flag": _flag, "squad": _squad, "region": _region, "pad": _pad, "dot": _dot}
