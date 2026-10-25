"""Makes samples/gemini.jfgproj/geometry/field.glb: the sample planet's ground, drawn from scratch (no game data).

    python samples/make_gemini_field.py       (needs the decomp setting: it writes with jfgformats' glTF writer)

A 2000 x 2000 grass field at height 0, a stone ramp and three stone platforms stepping up to the north (25, 50,
75), a pool of water to the east and a strip of lava to the west. It shows what a Blender export needs to give
Studio: triangles counter-clockwise seen from their front, COLOR_0 as the baked light, TEXCOORD_0 repeating past 1,
and the material-name words (`water`, `lava`). Three images (grass, stone, water): the ROM's 3D texture table has
room for exactly three new ones, so the lava reuses the stone image under red vertex colours.
"""

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jfgstudio import formats  # noqa: E402

OUT = Path(__file__).parent / "gemini.jfgproj" / "geometry" / "field.glb"
HALF, TILE = 1000, 200
PLATFORMS = [(-150, 300, 150, 500, 25), (-150, 550, 150, 750, 50), (-150, 800, 150, 1000, 75)]
RAMP = (-100, 100, 100, 300, 0, 25)
POOL = (400, -200, 800, 200, 6)
LAVA = (-900, -300, -750, 300, 1)


def image(png, kind: str, size: int, seed: int) -> bytes:
    rng = random.Random(seed)
    noise = {}
    px = bytearray()
    for y in range(size):
        for x in range(size):
            n = noise.setdefault((x // 4, y // 4), rng.randint(-16, 16))
            if kind == "grass":
                c = (80 + n, 150 + n, 60 + n // 2, 255)
            elif kind == "stone":
                seam = y % 16 == 0 or (x + (8 if (y // 16) % 2 else 0)) % 16 == 0
                v = 95 if seam else 155 + n
                c = (v, v, v + 10, 255)
            else:
                c = (50 + n // 2, 110 + n, 220, 150)
            px += bytes(max(0, min(255, v)) for v in c)
    return png.write_png("RGBA", size, size, bytes(px))


class Mesh:
    def __init__(self):
        self.parts = {}  # material -> (positions, uvs, colours)

    def quad(self, material, corners, colour, uv_scale, wall=False):
        pos, uvs, cols = self.parts.setdefault(material, ([], [], []))
        for tri in ((0, 1, 2), (0, 2, 3)):
            for k in tri:
                x, y, z = corners[k]
                pos.append((float(x), float(y), float(z)))
                u, v = (x + z, y) if wall else (x, z)
                uvs.append((u / uv_scale, v / uv_scale))
                cols.append(colour)

    def block(self, x0, z0, x1, z1, y0, y1):
        self.quad("stone", [(x0, y1, z0), (x0, y1, z1), (x1, y1, z1), (x1, y1, z0)], (255, 255, 255, 255), 100)
        for (ax, az), (bx, bz), s in (((x0, z0), (x1, z0), 205), ((x1, z0), (x1, z1), 175),
                                     ((x1, z1), (x0, z1), 205), ((x0, z1), (x0, z0), 175)):
            self.quad("stone_wall", [(bx, y0, bz), (ax, y0, az), (ax, y1, az), (bx, y1, bz)], (s, s, s, 255), 50,
                      wall=True)


def main() -> int:
    jf = formats.load()
    from jfgformats import gltf, png
    m = Mesh()
    for x in range(-HALF, HALF, TILE):
        for z in range(-HALF, HALF, TILE):
            if LAVA[0] <= x < LAVA[2] and LAVA[1] <= z < LAVA[3]:
                continue
            m.quad("grass", [(x, 0, z), (x, 0, z + TILE), (x + TILE, 0, z + TILE), (x + TILE, 0, z)],
                   (240, 240, 240, 255), 100)
    for p in PLATFORMS:
        m.block(p[0], p[1], p[2], p[3], 0, p[4])
    x0, z0, x1, z1, h0, h1 = RAMP
    m.quad("stone", [(x0, h0, z0), (x0, h1, z1), (x1, h1, z1), (x1, h0, z0)], (235, 235, 235, 255), 100)
    x0, z0, x1, z1, h = POOL
    m.quad("water", [(x0, h, z0), (x0, h, z1), (x1, h, z1), (x1, h, z0)], (255, 255, 255, 200), 100)
    x0, z0, x1, z1, h = LAVA
    m.quad("rock_lava", [(x0, h, z0), (x0, h, z1), (x1, h, z1), (x1, h, z0)], (255, 90, 30, 255), 60)

    images = {"grass": image(png, "grass", 16, 1), "stone": image(png, "stone", 32, 2),
              "water": image(png, "water", 16, 3)}
    image_of = {"grass": "grass", "stone": "stone", "stone_wall": "stone", "water": "water", "rock_lava": "stone"}
    flags = {"grass": 0x8, "stone": 0x8, "stone_wall": 0x1008, "water": 0x1200D, "rock_lava": 0x8008}
    g = gltf.GlbWriter(generator="JFG-Studio samples/make_gemini_field.py")
    doc = g.doc
    doc["images"], doc["textures"], doc["materials"] = [], [], []
    doc["samplers"] = [{"magFilter": 9729, "minFilter": 9729, "wrapS": 10497, "wrapT": 10497}]
    texture_index = {}
    for name in ("grass", "stone", "water"):
        doc["images"].append({"name": name, "mimeType": "image/png", "bufferView": g.view(images[name])})
        doc["textures"].append({"source": len(doc["images"]) - 1, "sampler": 0})
        texture_index[name] = len(doc["textures"]) - 1
    prims = []
    for name, (pos, uvs, cols) in m.parts.items():
        mat = {"name": name,
               "pbrMetallicRoughness": {"baseColorTexture": {"index": texture_index[image_of[name]]},
                                        "metallicFactor": 0.0, "roughnessFactor": 1.0},
               "extras": {"batchFlags": flags[name], "pass": "translucent" if name == "water" else "opaque"}}
        if name == "water":
            mat["alphaMode"] = "BLEND"
        doc["materials"].append(mat)
        prims.append({"attributes": {"POSITION": g.accessor("f", pos, 5126, "VEC3", bounds=True),
                                     "TEXCOORD_0": g.accessor("f", uvs, 5126, "VEC2"),
                                     "COLOR_0": g.accessor("B", cols, 5121, "VEC4", normalized=True)},
                      "mode": 4, "material": len(doc["materials"]) - 1})
    doc["meshes"] = [{"name": "field", "primitives": prims}]
    doc["nodes"] = [{"name": "field", "mesh": 0}]
    doc["scene"], doc["scenes"] = 0, [{"nodes": [0]}]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    g.write(str(OUT))
    print(f"wrote {OUT} ({sum(len(p[0]) for p in m.parts.values()) // 3} triangles), jfgformats {jf.__version__}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
