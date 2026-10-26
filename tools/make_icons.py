"""Renders Studio's icon and banner from their SVGs with a headless Chrome (or Edge), and packs the Windows icon:

    jfgstudio/assets/icon.svg   -> jfgstudio/assets/app_settings/icon.png (256: the window's icon, hello_imgui's
                                   assets folder) and icon.ico (16-256: the exe's)
    docs/images/banner.svg      -> docs/images/banner.png (the README's, 1280 x 320)

    python tools/make_icons.py [path to chrome.exe]

Run it after editing either SVG and commit what it writes.
"""

import os
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ICON_SVG = ROOT / "jfgstudio" / "assets" / "icon.svg"
BANNER_SVG = ROOT / "docs" / "images" / "banner.svg"
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
BROWSERS = [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            "google-chrome", "chromium", "chrome", "msedge"]


def browser(given: str | None) -> str:
    for b in ([given] if given else []) + BROWSERS:
        if b and (Path(b).is_file() or shutil.which(b)):
            return b
    sys.exit("no Chrome or Edge found: pass its path")


def fonts_css() -> str:
    """@font-face rules for the port's fonts the banner names: JFG Black (its title) and Lato (the tagline). They are
    read from the port (the `port` setting, else the port repo by JFG-Modding-Tools) and never kept in Studio."""
    from jfgstudio.gui import theme
    css = ""
    for family, weight, parts in (("JFG Black", 400, ("jfg_launcher", "JFG-Black.ttf")),
                                  ("Lato", 700, ("LatoLatin-Bold.ttf",)), ("Lato", 400, ("LatoLatin-Regular.ttf",))):
        path = theme._port_font(*parts)
        if path is None:
            print(f"warning: the port's {'/'.join(parts)} was not found: the banner falls back to another font")
            continue
        css += f"@font-face{{font-family:'{family}';font-weight:{weight};src:url('{path.as_uri()}')}}"
    return css


def render(exe: str, svg: Path, width: int, height: int, out: Path) -> None:
    """The SVG drawn at width x height on a transparent page, as a PNG. It is put in the page itself (not an <img>),
    so the page's fonts apply, with its folder as the base its links (the banner's icon) resolve against."""
    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "page.html"
        body = svg.read_text(encoding="utf-8")
        body = body.replace("<svg ", f"<svg style='display:block;width:{width}px;height:{height}px' ", 1)
        page.write_text(f"<!doctype html><html><head><base href='{svg.parent.as_uri()}/'><style>{fonts_css()}"
                        f"html,body{{margin:0;background:transparent}}</style></head><body>{body}</body></html>",
                        encoding="utf-8")
        shot = Path(tmp) / "shot.png"
        subprocess.run([exe, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=1",
                        "--default-background-color=00000000", f"--window-size={width},{height}",
                        "--allow-file-access-from-files", "--virtual-time-budget=3000",
                        f"--user-data-dir={Path(tmp) / 'profile'}", f"--screenshot={shot}", page.as_uri()],
                       check=True, capture_output=True, timeout=120)
        shutil.copyfile(shot, out)


def pack_ico(pngs: list[tuple[int, bytes]]) -> bytes:
    """An .ico of PNG images (Windows Vista and later read them), one per size."""
    head = struct.pack("<HHH", 0, 1, len(pngs))
    offset = len(head) + 16 * len(pngs)
    entries, data = b"", b""
    for size, png in pngs:
        entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(png), offset + len(data))
        data += png
    return head + entries + data


def main() -> None:
    exe = browser(sys.argv[1] if len(sys.argv) > 1 else None)
    assets = ICON_SVG.parent
    pngs = []
    with tempfile.TemporaryDirectory() as tmp:
        for size in ICO_SIZES:
            out = Path(tmp) / f"icon{size}.png"
            render(exe, ICON_SVG, size, size, out)
            pngs.append((size, out.read_bytes()))
    (assets / "app_settings" / "icon.png").write_bytes(pngs[-1][1])
    (assets / "icon.ico").write_bytes(pack_ico(pngs))
    render(exe, BANNER_SVG, 1280, 320, BANNER_SVG.with_suffix(".png"))
    for p in (assets / "app_settings" / "icon.png", assets / "icon.ico", BANNER_SVG.with_suffix(".png")):
        print(f"{p.relative_to(ROOT)}: {os.path.getsize(p)} bytes")


if __name__ == "__main__":
    main()
