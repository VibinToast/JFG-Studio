"""The game's own textures, from the player's ROM: a library levels can use without a pack carrying them.

A pack may not contain the game's art. A level made with the game's level textures still can be: the pack names
those textures by number, and each player's game shows them from their own ROM. The library (in the player's
catalog folder, never in a project) holds every texture the game's levels use as a PNG, the exact bytes the format
library writes, with what each looks like and where the game uses it, so an agent can pick textures for a theme and
Blender can paint with them.

Blender may write an image back with other bytes than it read, so images are matched by their pixels: before a
track is imported, `prepare_glb` swaps every image whose pixels are a game level texture for the game's own PNG,
which the importer then names by number (jfgformats `import_track`, stock_textures). Any other image whose pixels
are the game's (a model's texture, say) is game art the pack would carry: `game_art` finds it and the build refuses.
"""

from __future__ import annotations

import colorsys
import hashlib
import json
import struct
import tempfile
from pathlib import Path

LIBRARY_FORMAT = 2  # 2: "repeat" per texture


def pixel_key(png: bytes) -> str:
    """What an image looks like, whatever its PNG bytes: a hash of its size and RGBA pixels."""
    from jfgformats import png as pngmod
    w, h, rgba = pngmod.read_png(png)
    return hashlib.sha1(struct.pack(">II", w, h) + bytes(rgba)).hexdigest()


def colour_words(r: float, g: float, b: float) -> list[str]:
    """Plain words for an average colour (0-255): its hue (or grey, black, white) and how bright it is."""
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    if v < 0.15:
        hue = "black"
    elif s < 0.18:
        hue = "white" if v > 0.85 else "grey"
    else:
        deg = h * 360
        hue = ("red" if deg < 15 or deg >= 345 else "orange" if deg < 40 else "yellow" if deg < 70
               else "green" if deg < 165 else "cyan" if deg < 200 else "blue" if deg < 255
               else "purple" if deg < 290 else "pink")
        if hue in ("orange", "red", "yellow") and v < 0.55:
            hue = "brown"
    shade = "dark" if v < 0.35 else "bright" if v > 0.75 else "mid"
    return [hue, shade] + (["vivid"] if s > 0.6 and v > 0.35 else [])


class Library:
    """The game's level textures in the player's catalog folder (`textures/`): index.json, a PNG per texture and
    art.json (the pixel keys of every texture of the game, to keep game art out of packs)."""

    def __init__(self, catalog):
        self.catalog = catalog
        self.dir = Path(catalog.dir) / "textures"
        self._index: dict | None = None
        self._by_key: dict | None = None
        self._art: set | None = None

    # building
    def _models(self):
        from jfgformats import models
        return models.ModelData(self.catalog.assets)

    def build(self, log=print) -> Path:
        """Writes the library (a few seconds; once per ROM and library format)."""
        from jfgformats import png as pngmod
        from jfgformats import tracks
        self.dir.mkdir(parents=True, exist_ok=True)
        data = self._models()
        track_data = tracks.TrackData(self.catalog.assets)
        levels_of: dict[int, list[int]] = {}
        for lv in self.catalog.levels():
            levels_of.setdefault(lv["track"], []).append(lv["id"])
        textures: dict[int, dict] = {}
        for track in range(track_data.count):
            try:
                entries = tracks.read_track(track_data.track(track))["textures"]
            except Exception:  # noqa: BLE001 (a track the format library can't read: its textures are left out)
                continue
            for entry in entries:
                t = textures.setdefault(entry[0], {"entry": list(entry), "tracks": [], "levels": []})
                if track not in t["tracks"]:
                    t["tracks"].append(track)
                    t["levels"] += [n for n in levels_of.get(track, []) if n not in t["levels"]]
        from jfgformats.core import unpack_texture_entry
        for tex, t in textures.items():
            png, alpha, w, h = data.texture_png(tex | 0x8000)
            _, header = unpack_texture_entry(data.tex_data[data.tex_table[tex]:data.tex_table[tex + 1]])
            # The texture's own wrap masks (header +0x1D S, +0x1F T; 0 clamps): most of the game's level textures
            # clamp and are mapped once per polygon. Past one repeat on a clamped axis they smear (play-tested).
            t["repeat"] = [header[0x1D] != 0, header[0x1F] != 0]
            (self.dir / f"{tex:04X}.png").write_bytes(png)
            _, _, rgba = pngmod.read_png(png)
            px = memoryview(bytes(rgba))
            n = max(1, len(px) // 4)
            r, g, b = (sum(px[i::4]) / n for i in range(3))
            t.update({"id": tex, "width": w, "height": h, "alpha": alpha or "none", "key": pixel_key(png),
                      "rgb": [round(r), round(g), round(b)], "words": colour_words(r, g, b),
                      "file": f"{tex:04X}.png"})
        index = {"format": LIBRARY_FORMAT, "textures": {f"{k:04X}": v for k, v in sorted(textures.items())}}
        (self.dir / "index.json").write_text(json.dumps(index), encoding="utf-8")
        art = sorted({pixel_key(data.texture_png(i | 0x8000)[0]) for i in range(data.tex_count)})
        (self.dir / "art.json").write_text(json.dumps({"format": LIBRARY_FORMAT, "keys": art}), encoding="utf-8")
        self._index = self._by_key = self._art = None
        log(f"game textures: {len(textures)} level textures, {len(art)} images of the game -> {self.dir}")
        return self.dir

    def _load(self) -> None:
        path = self.dir / "index.json"
        doc = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
        if not doc or doc.get("format") != LIBRARY_FORMAT or not (self.dir / "art.json").exists():
            self.build(log=lambda _: None)
            doc = json.loads(path.read_text(encoding="utf-8"))
        self._index = {int(k, 16): v for k, v in doc["textures"].items()}
        self._by_key = {v["key"]: v for v in self._index.values()}
        self._art = set(json.loads((self.dir / "art.json").read_text(encoding="utf-8"))["keys"])

    # reading
    def textures(self) -> dict[int, dict]:
        """Texture id -> {entry, width, height, alpha, rgb, words, tracks, levels, file, key}."""
        if self._index is None:
            self._load()
        return self._index

    def path(self, tex: int) -> Path:
        return self.dir / f"{tex:04X}.png"

    def level_texture(self, png: bytes) -> dict | None:
        """The game level texture with these pixels, or None."""
        if self._by_key is None:
            self._load()
        try:
            return self._by_key.get(pixel_key(png))
        except Exception:  # noqa: BLE001 (not a PNG the format library reads: not the game's)
            return None

    def game_art(self, png: bytes) -> bool:
        """An image whose pixels are one of the game's textures (any of them)."""
        if self._art is None:
            self._load()
        try:
            return pixel_key(png) in self._art
        except Exception:  # noqa: BLE001
            return False

    def prepare_glb(self, path: Path) -> tuple[Path, list[int]]:
        """`path`, or a temporary copy whose images with a game level texture's pixels are that texture's own PNG
        bytes (so the importer names it by number), and the texture ids it found."""
        from jfgformats import gltf
        doc, blob = gltf.read_glb(str(path))
        swaps, found = {}, []
        for image in doc.get("images", []):
            if "bufferView" not in image:
                continue
            view = doc["bufferViews"][image["bufferView"]]
            start = view.get("byteOffset", 0)
            png = bytes(blob[start:start + view["byteLength"]])
            hit = self.level_texture(png)
            if hit is not None:
                found.append(hit["id"])
                stock = self.path(hit["id"]).read_bytes()
                if stock != png:
                    swaps[image["bufferView"]] = stock
        if not swaps:
            return path, found
        out = gltf.GlbWriter()
        out.doc = {k: v for k, v in doc.items() if k not in ("bufferViews", "buffers")}
        out.doc["bufferViews"], out.doc["buffers"] = [], [{"byteLength": 0}]
        for n, view in enumerate(doc["bufferViews"]):
            start = view.get("byteOffset", 0)
            body = swaps.get(n, bytes(blob[start:start + view["byteLength"]]))
            new = out.view(body, view.get("target"))
            if "byteStride" in view:
                out.doc["bufferViews"][new]["byteStride"] = view["byteStride"]
        tmp = Path(tempfile.mkdtemp(prefix="jfgstudio-")) / path.name
        tmp.write_bytes(out.to_bytes())
        return tmp, found

    # where the game's levels use them
    def track_usage(self, track: int) -> dict[int, dict]:
        """How a game track uses its level textures: {texture id: {"area", "floor", "wall", "ceiling"}} (game units
        squared; floor faces point up, ceilings down, the rest are walls). Cached in the library."""
        cache = self.dir / "usage" / f"{track:03d}.json"
        if cache.exists():
            return {int(k): v for k, v in json.loads(cache.read_text(encoding="utf-8")).items()}
        from jfgformats import gltf
        doc, blob = gltf.read_glb(str(self.catalog.track_glb(track)))
        ids: dict[int, int | None] = {}
        usage: dict[int, dict] = {}
        for mesh in doc.get("meshes", []):
            for prim in mesh.get("primitives", []):
                mat = doc["materials"][prim["material"]] if prim.get("material") is not None else {}
                base = (mat.get("pbrMetallicRoughness") or {}).get("baseColorTexture")
                if base is None:
                    continue
                if base["index"] not in ids:
                    hit = self.level_texture(gltf.image_bytes(doc, blob, base["index"]))
                    ids[base["index"]] = hit["id"] if hit else None
                tex = ids[base["index"]]
                if tex is None or "POSITION" not in prim.get("attributes", {}):
                    continue
                pos = gltf.read_accessor(doc, blob, prim["attributes"]["POSITION"])
                idx = gltf.read_accessor(doc, blob, prim["indices"]) if "indices" in prim else range(len(pos))
                u = usage.setdefault(tex, {"area": 0.0, "floor": 0.0, "wall": 0.0, "ceiling": 0.0})
                idx = [v[0] if isinstance(v, tuple) else v for v in idx]
                for i in range(0, len(idx) - 2, 3):
                    a, b, c = (pos[idx[i + k]] for k in range(3))
                    e1 = [b[n] - a[n] for n in range(3)]
                    e2 = [c[n] - a[n] for n in range(3)]
                    nx, ny, nz = (e1[1] * e2[2] - e1[2] * e2[1], e1[2] * e2[0] - e1[0] * e2[2],
                                  e1[0] * e2[1] - e1[1] * e2[0])
                    area = (nx * nx + ny * ny + nz * nz) ** 0.5 / 2
                    if area <= 0:
                        continue
                    up = ny / (2 * area)  # glTF is Y up, as the game
                    u["area"] += area
                    u["floor" if up > 0.6 else "ceiling" if up < -0.6 else "wall"] += area
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({str(k): {n: round(x) for n, x in v.items()} for k, v in usage.items()}),
                         encoding="utf-8")
        return {k: {n: round(x) for n, x in v.items()} for k, v in usage.items()}

    def sheet(self, ids: list[int], cell: int = 64, columns: int = 8) -> bytes:
        """A PNG of textures side by side (each scaled to `cell` pixels, small ones repeated to fill it), with its id
        in hex under each."""
        import numpy as np
        from jfgformats import png as pngmod
        rows = max(1, -(-len(ids) // columns))
        label = 14
        out = np.full((rows * (cell + label + 4), columns * (cell + 4), 3), 24, np.uint8)
        for n, tex in enumerate(ids):
            w, h, rgba = pngmod.read_png(self.path(tex).read_bytes())
            img = np.frombuffer(bytes(rgba), np.uint8).reshape(h, w, 4)
            ys = (np.arange(cell) * h // max(cell, h) if h > cell else np.arange(cell) % h)
            xs = (np.arange(cell) * w // max(cell, w) if w > cell else np.arange(cell) % w)
            tile = img[ys][:, xs]
            check = ((np.arange(cell)[:, None] // 8 + np.arange(cell)[None, :] // 8) % 2 * 40 + 70)[..., None]
            alpha = tile[..., 3:4] / 255.0
            rgb = tile[..., :3] * alpha + check * (1 - alpha)  # see-through parts over a checkerboard
            y0, x0 = (n // columns) * (cell + label + 4) + 2, (n % columns) * (cell + 4) + 2
            out[y0:y0 + cell, x0:x0 + cell] = rgb.astype(np.uint8)
            _text(out, x0 + 2, y0 + cell + 2, f"{tex:04X}")
        return _png_rgb(out)


# A 3 x 5 pixel font for the hex digits of the labels.
_GLYPHS = {"0": "111101101101111", "1": "010110010010111", "2": "111001111100111", "3": "111001111001111",
           "4": "101101111001001", "5": "111100111001111", "6": "111100111101111", "7": "111001001001001",
           "8": "111101111101111", "9": "111101111001111", "A": "010101111101101", "B": "110101110101110",
           "C": "011100100100011", "D": "110101101101110", "E": "111100110100111", "F": "111100110100100"}


def _text(img, x: int, y: int, s: str, scale: int = 2) -> None:
    for k, ch in enumerate(s):
        bits = _GLYPHS.get(ch)
        if bits is None:
            continue
        for i, bit in enumerate(bits):
            if bit == "1":
                yy, xx = y + (i // 3) * scale, x + (k * 4 + i % 3) * scale
                img[yy:yy + scale, xx:xx + scale] = (235, 235, 235)


def _png_rgb(img) -> bytes:
    import zlib
    h, w, _ = img.shape
    raw = b"".join(b"\0" + img[y].tobytes() for y in range(h))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))
