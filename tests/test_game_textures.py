"""The game-texture library with made-up textures (no ROM): pixel matching, the art check, the glb swap, sheets."""

import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from jfgstudio import formats
from jfgstudio import game_textures as gt


def _png(w, h, rgba, level=6):
    raw = b"".join(b"\0" + bytes(rgba[y * w * 4:(y + 1) * w * 4]) for y in range(h))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, level)) + chunk(b"IEND", b""))


GRASS = bytes([40, 160, 60, 255] * 64)  # 8 x 8, the "game's" level texture
MODEL = bytes([200, 30, 30, 255] * 16)  # 4 x 4, a "game" texture no level uses
MINE = bytes([10, 10, 200, 255] * 64)   # 8 x 8, the player's own


class FakeCatalog:
    def __init__(self, folder):
        self.dir = folder


class Library(unittest.TestCase):
    def setUp(self):
        try:
            formats.load()
        except formats.FormatsUnavailable as e:
            self.skipTest(str(e))
        self.tmp = tempfile.TemporaryDirectory()
        self.lib = gt.Library(FakeCatalog(self.tmp.name))
        self.lib.dir.mkdir()
        grass = _png(8, 8, GRASS)
        (self.lib.dir / "0012.png").write_bytes(grass)
        index = {"format": gt.LIBRARY_FORMAT, "textures": {"0012": {
            "id": 0x12, "entry": [0x12, 8, 8, 0], "width": 8, "height": 8, "alpha": "none", "rgb": [40, 160, 60],
            "words": gt.colour_words(40, 160, 60), "tracks": [3], "levels": [5], "file": "0012.png",
            "key": gt.pixel_key(grass)}}}
        (self.lib.dir / "index.json").write_text(json.dumps(index), encoding="utf-8")
        art = [gt.pixel_key(grass), gt.pixel_key(_png(4, 4, MODEL))]
        (self.lib.dir / "art.json").write_text(json.dumps({"format": gt.LIBRARY_FORMAT, "keys": art}), "utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_matched_by_pixels_not_bytes(self):
        recoded = _png(8, 8, GRASS, level=1)  # Blender may write other bytes for the same pixels
        self.assertNotEqual(recoded, (self.lib.dir / "0012.png").read_bytes())
        self.assertEqual(self.lib.level_texture(recoded)["id"], 0x12)
        self.assertIsNone(self.lib.level_texture(_png(8, 8, MINE)))
        self.assertTrue(self.lib.game_art(_png(4, 4, MODEL, level=9)))  # the game's, though no level uses it
        self.assertFalse(self.lib.game_art(_png(8, 8, MINE)))

    def test_colour_words(self):
        self.assertEqual(gt.colour_words(40, 160, 60)[0], "green")
        self.assertEqual(gt.colour_words(120, 80, 40)[0], "brown")
        self.assertEqual(gt.colour_words(128, 128, 128)[0], "grey")
        self.assertIn("dark", gt.colour_words(10, 15, 60))

    def test_glb_images_become_the_games_own(self):
        from jfgformats import gltf
        g = gltf.GlbWriter()
        recoded, mine = _png(8, 8, GRASS, level=1), _png(8, 8, MINE)
        pos = g.accessor("f", [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0)], 5126, "VEC3", bounds=True)
        g.doc["images"] = [{"bufferView": g.view(recoded), "mimeType": "image/png"},
                           {"bufferView": g.view(mine), "mimeType": "image/png"}]
        g.doc["meshes"] = [{"primitives": [{"attributes": {"POSITION": pos}}]}]
        src = Path(self.tmp.name) / "level.glb"
        g.write(src)
        out, found = self.lib.prepare_glb(src)
        self.assertEqual(found, [0x12])
        doc, blob = gltf.read_glb(str(out))
        views = [doc["bufferViews"][i["bufferView"]] for i in doc["images"]]
        images = [bytes(blob[v.get("byteOffset", 0):v.get("byteOffset", 0) + v["byteLength"]]) for v in views]
        self.assertEqual(images, [(self.lib.dir / "0012.png").read_bytes(), mine])  # the game's bytes; ours kept
        self.assertEqual(gltf.read_accessor(doc, blob, pos)[1], (1.0, 0.0, 0.0))  # the rest of the file intact
        plain = Path(self.tmp.name) / "plain.glb"
        g.doc["images"] = [g.doc["images"][1]]
        g.write(plain)
        self.assertEqual(self.lib.prepare_glb(plain), (plain, []))  # nothing of the game's: the file as it is

    def test_sheet(self):
        from jfgformats import png as pngmod
        w, h, _ = pngmod.read_png(self.lib.sheet([0x12, 0x12, 0x12], cell=32, columns=2))
        self.assertEqual((w, h), (2 * 36, 2 * (32 + 14 + 4)))


if __name__ == "__main__":
    unittest.main()
