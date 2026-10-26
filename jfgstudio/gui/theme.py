"""The JFG theme: the port launcher's colours (assets/launcher.rml in the port) applied to every ImGui colour.

Fonts: Lato for the body and the JFG pixel font for titles, both loaded from the user's port install when the `port`
setting is set (Studio does not ship them; the JFG font's licence is still open). Without it, ImGui's default font.
"""

from pathlib import Path

from imgui_bundle import ImVec4, imgui

from jfgstudio import settings


def _rgb(hex_colour: str, alpha: float = 1.0) -> ImVec4:
    h = hex_colour.lstrip("#")
    return ImVec4(int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255, alpha)


# The launcher palette (design doc section 2.2).
BACKGROUND = "#050B1C"
PANEL = "#0B1733"
FRAME = "#0A2660"
BORDER = "#1F3D73"
ACCENT = "#3CC8FF"
BUTTON = "#1E90E8"
HIGHLIGHT = "#FFB21A"
TEXT = "#E8F3FF"
MUTED = "#8199BF"
ERROR = "#FF4A4A"

TITLE_FONT: imgui.ImFont | None = None


def colour(name: str, alpha: float = 1.0) -> ImVec4:
    return _rgb(globals()[name], alpha)


def apply_style() -> None:
    style = imgui.get_style()
    c = imgui.Col_
    pairs = {
        c.text: _rgb(TEXT), c.text_disabled: _rgb(MUTED), c.text_link: _rgb(ACCENT),
        c.window_bg: _rgb(PANEL), c.child_bg: _rgb(PANEL, 0.0), c.popup_bg: _rgb(PANEL, 0.98),
        c.border: _rgb(BORDER), c.border_shadow: _rgb(BACKGROUND, 0.0),
        c.frame_bg: _rgb(FRAME), c.frame_bg_hovered: _rgb(BORDER), c.frame_bg_active: _rgb(BUTTON, 0.6),
        c.title_bg: _rgb(BACKGROUND), c.title_bg_active: _rgb(FRAME), c.title_bg_collapsed: _rgb(BACKGROUND),
        c.menu_bar_bg: _rgb(BACKGROUND),
        c.scrollbar_bg: _rgb(BACKGROUND, 0.6), c.scrollbar_grab: _rgb(BORDER),
        c.scrollbar_grab_hovered: _rgb(BUTTON), c.scrollbar_grab_active: _rgb(ACCENT),
        c.check_mark: _rgb(ACCENT), c.slider_grab: _rgb(BUTTON), c.slider_grab_active: _rgb(ACCENT),
        c.button: _rgb(BUTTON, 0.55), c.button_hovered: _rgb(BUTTON, 0.85), c.button_active: _rgb(ACCENT),
        c.header: _rgb(FRAME), c.header_hovered: _rgb(BUTTON, 0.55), c.header_active: _rgb(ACCENT, 0.7),
        c.separator: _rgb(BORDER), c.separator_hovered: _rgb(BUTTON), c.separator_active: _rgb(ACCENT),
        c.resize_grip: _rgb(BORDER, 0.5), c.resize_grip_hovered: _rgb(BUTTON), c.resize_grip_active: _rgb(ACCENT),
        c.tab: _rgb(BACKGROUND), c.tab_hovered: _rgb(BUTTON, 0.7), c.tab_selected: _rgb(FRAME),
        c.tab_selected_overline: _rgb(ACCENT), c.tab_dimmed: _rgb(BACKGROUND),
        c.tab_dimmed_selected: _rgb(PANEL), c.tab_dimmed_selected_overline: _rgb(BORDER),
        c.docking_preview: _rgb(ACCENT, 0.5), c.docking_empty_bg: _rgb(BACKGROUND),
        c.plot_lines: _rgb(ACCENT), c.plot_lines_hovered: _rgb(HIGHLIGHT),
        c.plot_histogram: _rgb(BUTTON), c.plot_histogram_hovered: _rgb(HIGHLIGHT),
        c.table_header_bg: _rgb(FRAME), c.table_border_strong: _rgb(BORDER), c.table_border_light: _rgb(BORDER, 0.5),
        c.table_row_bg: _rgb(PANEL, 0.0), c.table_row_bg_alt: _rgb(FRAME, 0.35),
        c.text_selected_bg: _rgb(ACCENT, 0.35), c.drag_drop_target: _rgb(HIGHLIGHT),
        c.nav_cursor: _rgb(ACCENT), c.nav_windowing_highlight: _rgb(ACCENT, 0.7),
        c.nav_windowing_dim_bg: _rgb(BACKGROUND, 0.6), c.modal_window_dim_bg: _rgb(BACKGROUND, 0.7),
    }
    for idx, value in pairs.items():
        style.set_color_(idx, value)
    style.window_rounding = 6.0
    style.child_rounding = 6.0
    style.frame_rounding = 5.0
    style.popup_rounding = 6.0
    style.grab_rounding = 5.0
    style.tab_rounding = 5.0
    style.scrollbar_rounding = 6.0
    style.window_border_size = 1.0
    style.frame_border_size = 0.0
    style.window_padding = imgui.ImVec2(10, 8)
    style.frame_padding = imgui.ImVec2(8, 5)
    style.item_spacing = imgui.ImVec2(8, 6)


def _port_font(*parts: str) -> Path | None:
    """A font from the port: the `port` setting's folder, else the port repo next to JFG-Modding-Tools."""
    bases = []
    port = settings.get("port")
    if port:
        bases += [Path(port), Path(port) / ".."]
    bases.append(settings._workspace() / "Recomp")
    for base in bases:
        p = base.joinpath("assets", *parts)
        if p.is_file():
            return p
    return None


TOOLTIP_WIDTH = 26  # tooltips wrap at this many font heights
TOOLTIP_SCALE = 0.88  # and are a little smaller than the window text


def tip(text: str) -> None:
    """A tooltip as a box of wrapped, slightly smaller text (imgui.set_tooltip is one long line)."""
    imgui.begin_tooltip()
    imgui.push_font(None, imgui.get_style().font_size_base * TOOLTIP_SCALE)
    imgui.push_text_wrap_pos(imgui.get_font_size() * TOOLTIP_WIDTH)
    imgui.text_unformatted(text)
    imgui.pop_text_wrap_pos()
    imgui.pop_font()
    imgui.end_tooltip()


def load_fonts() -> None:
    """hello_imgui's load_additional_fonts callback."""
    global TITLE_FONT
    io = imgui.get_io()
    body = _port_font("LatoLatin-Regular.ttf")
    if body:
        io.fonts.add_font_from_file_ttf(str(body), 17.0)
    else:
        io.fonts.add_font_default()
    title = _port_font("jfg_launcher", "JFG-Black.ttf")
    TITLE_FONT = io.fonts.add_font_from_file_ttf(str(title), 20.0) if title else None
