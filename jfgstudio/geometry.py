"""Edits to a pack's geometry (.glb) that Studio makes itself, so simple things need no trip to Blender (task S17).

Water planes: a flat quad with a `water` material (batch flag 0x2000, translucent), the way the game draws water: a
surface, not a volume; its height is the swim height (design doc §5.3). The quad reuses the file's own water
material when it has one, so it adds no new texture (the game's 3D texture table has little room).
"""

import json
import random
import struct

from jfgstudio import formats

WATER_FLAGS = 0x1200D  # what the sample's water uses: water (0x2000), translucent, double-sided
WATER_UV = 100.0  # world units per texture repeat


class GeometryError(ValueError):
    pass


def _words(name: str) -> set[str]:
    return set("".join(ch if ch.isalnum() else " " for ch in name.lower()).split())


def _water_image(png) -> bytes:
    rng = random.Random(3)
    px = bytearray()
    for _y in range(16):
        for _x in range(16):
            n = rng.randint(-12, 12)
            px += bytes((max(0, 50 + n // 2), max(0, 110 + n), 220, 150))
    return png.write_png("RGBA", 16, 16, bytes(px))


def add_water(data: bytes, x0: float, z0: float, x1: float, z1: float, height: float) -> bytes:
    """The .glb with one more water quad from (x0, z0) to (x1, z1) at `height`."""
    formats.load()
    from jfgformats import gltf, png
    if abs(x1 - x0) < 1 or abs(z1 - z0) < 1:
        raise GeometryError("the water rectangle is empty")
    doc, blob = gltf.parse_glb(data, "geometry")
    g = gltf.GlbWriter()
    g.doc = doc
    g.buffer = bytearray(blob)
    doc.setdefault("bufferViews", [])
    doc.setdefault("accessors", [])
    doc["buffers"] = [{"byteLength": len(blob)}]
    materials = doc.setdefault("materials", [])
    mat = next((i for i, m in enumerate(materials) if "water" in _words(m.get("name", ""))), None)
    if mat is None:
        doc.setdefault("images", []).append({"name": "water", "mimeType": "image/png",
                                             "bufferView": g.view(_water_image(png))})
        samplers = doc.setdefault("samplers", [])
        samplers.append({"magFilter": 9729, "minFilter": 9729, "wrapS": 10497, "wrapT": 10497})
        doc.setdefault("textures", []).append({"source": len(doc["images"]) - 1, "sampler": len(samplers) - 1})
        materials.append({"name": "water",
                          "pbrMetallicRoughness": {"baseColorTexture": {"index": len(doc["textures"]) - 1},
                                                   "metallicFactor": 0.0, "roughnessFactor": 1.0},
                          "alphaMode": "BLEND",
                          "extras": {"batchFlags": WATER_FLAGS, "pass": "translucent"}})
        mat = len(materials) - 1
    xa, xb = sorted((float(x0), float(x1)))
    za, zb = sorted((float(z0), float(z1)))
    y = float(height)
    corners = [(xa, y, za), (xa, y, zb), (xb, y, zb), (xb, y, za)]  # counter-clockwise seen from above
    pos = [corners[k] for k in (0, 1, 2, 0, 2, 3)]
    uvs = [(x / WATER_UV, z / WATER_UV) for x, _, z in pos]
    cols = [(255, 255, 255, 200)] * 6
    prim = {"attributes": {"POSITION": g.accessor("f", pos, 5126, "VEC3", bounds=True),
                           "TEXCOORD_0": g.accessor("f", uvs, 5126, "VEC2"),
                           "COLOR_0": g.accessor("B", cols, 5121, "VEC4", normalized=True)},
            "mode": 4, "material": mat}
    n = sum(1 for node in doc.get("nodes", []) if node.get("name", "").startswith("water_plane"))
    doc.setdefault("meshes", []).append({"name": f"water_plane{n + 1}", "primitives": [prim]})
    doc.setdefault("nodes", []).append({"name": f"water_plane{n + 1}", "mesh": len(doc["meshes"]) - 1})
    scenes = doc.setdefault("scenes", [{"nodes": []}])
    scenes[doc.get("scene", 0)].setdefault("nodes", []).append(len(doc["nodes"]) - 1)
    return g.to_bytes()


def glb_json(data: bytes) -> dict:
    """The JSON chunk of a .glb (for tests and reports)."""
    length = struct.unpack_from("<I", data, 12)[0]
    return json.loads(data[20:20 + length])
