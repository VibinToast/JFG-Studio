"""A small kit for drawing level geometry in Python (the example world's levels): boxes, ramps, walls, discs and
height-mapped ground, lit with vertex colours, written as a .glb with jfgformats' glTF writer. No game data: every
image is drawn here.

Conventions (as in Blender's glTF export and the game): Y up, one unit = one game unit, triangles counter-clockwise
seen from their front. Material names carry the surface words the importer reads (`water`, `lava`, `kill`,
`collider`).
"""

import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jfgstudio import formats  # noqa: E402

SUN = (-0.45, -0.8, 0.35)  # direction the light travels
FLAGS = {"opaque": 0x1008, "wall": 0x1008, "water": 0x1200D, "lava": 0x9008, "kill": 0x80001008, "collider": 0x1408}


def _norm(v):
    n = math.sqrt(sum(c * c for c in v)) or 1.0
    return tuple(c / n for c in v)


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _sub(a, b):
    return tuple(x - y for x, y in zip(a, b, strict=True))


class Mesh:
    """Triangles by material: positions, UVs, colours. Lighting is baked as it is added."""

    def __init__(self, ambient=(0.42, 0.42, 0.48), sun=(0.75, 0.72, 0.62), sun_dir=SUN):
        self.parts = {}
        self.ambient, self.sun = ambient, sun
        self.to_light = _norm(tuple(-c for c in sun_dir))
        self.kinds = {}  # material -> surface kind (FLAGS key)
        self.images = {}  # material -> image name

    def material(self, name, image, kind="opaque"):
        self.kinds[name] = kind
        self.images[name] = image

    def _light(self, normal, tint, extra=1.0):
        d = max(0.0, sum(a * b for a, b in zip(normal, self.to_light, strict=True)))
        return tuple(max(0, min(255, round(255 * min(1.0, (self.ambient[i] + self.sun[i] * d) * extra) * tint[i])))
                     for i in range(3))

    def tri(self, mat, a, b, c, uv=None, tint=(1, 1, 1), alpha=255, lit=True):
        pos, uvs, cols = self.parts.setdefault(mat, ([], [], []))
        n = _norm(_cross(_sub(b, a), _sub(c, a)))
        col = (*self._light(n, tint), alpha) if lit else (*[round(255 * t) for t in tint], alpha)
        for k, p in enumerate((a, b, c)):
            pos.append(tuple(float(v) for v in p))
            uvs.append(uv[k] if uv else (p[0] / 100.0, p[2] / 100.0))
            cols.append(col)

    def quad(self, mat, a, b, c, d, scale=100.0, tint=(1, 1, 1), wall=False, alpha=255, lit=True):
        """a-b-c-d counter-clockwise seen from the front."""
        def uv(p):
            if wall:
                return ((p[0] + p[2]) / scale, -p[1] / scale)
            return (p[0] / scale, p[2] / scale)
        self.tri(mat, a, b, c, [uv(a), uv(b), uv(c)], tint, alpha, lit)
        self.tri(mat, a, c, d, [uv(a), uv(c), uv(d)], tint, alpha, lit)

    def floor(self, mat, x0, z0, x1, z1, y, scale=100.0, tint=(1, 1, 1), alpha=255, step=None):
        """A flat floor facing up (split in tiles of `step` so big floors keep their vertex light and segments)."""
        step = step or max(x1 - x0, z1 - z0)
        x = x0
        while x < x1:
            z = z0
            xe = min(x + step, x1)
            while z < z1:
                ze = min(z + step, z1)
                self.quad(mat, (x, y, z), (x, y, ze), (xe, y, ze), (xe, y, z), scale, tint, alpha=alpha)
                z = ze
            x = xe

    def wall(self, mat, ax, az, bx, bz, y0, y1, scale=100.0, tint=(1, 1, 1)):
        """A vertical wall from (ax, az) to (bx, bz): its front faces left of the a -> b direction."""
        self.quad(mat, (bx, y0, bz), (ax, y0, az), (ax, y1, az), (bx, y1, bz), scale, tint, wall=True)

    def box(self, top, side, x0, z0, x1, z1, y0, y1, scale=100.0, tint=(1, 1, 1), bottom=False):
        """A solid block: top and four outer walls (and the bottom for floating blocks)."""
        self.floor(top, x0, z0, x1, z1, y1, scale, tint)
        self.wall(side, x0, z0, x1, z0, y0, y1, scale, tint)
        self.wall(side, x1, z0, x1, z1, y0, y1, scale, tint)
        self.wall(side, x1, z1, x0, z1, y0, y1, scale, tint)
        self.wall(side, x0, z1, x0, z0, y0, y1, scale, tint)
        if bottom:
            self.quad(side, (x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1), scale, tint)

    def ramp(self, top, side, x0, z0, x1, z1, y0, y1, axis="z", scale=100.0):
        """A ramp rising from y0 to y1 along +z (axis "z") or +x, with side walls down to y0."""
        if axis == "z":
            a, b, c, d = (x0, y0, z0), (x0, y1, z1), (x1, y1, z1), (x1, y0, z0)
            self.quad(top, a, b, c, d, scale)
            self.tri(side, (x0, y0, z0), (x0, y0, z1), (x0, y1, z1))
            self.tri(side, (x1, y0, z0), (x1, y1, z1), (x1, y0, z1))
            self.wall(side, x1, z1, x0, z1, y0, y1, scale)
        else:
            a, b, c, d = (x0, y0, z0), (x0, y0, z1), (x1, y1, z1), (x1, y1, z0)
            self.quad(top, a, b, c, d, scale)
            self.tri(side, (x0, y0, z0), (x1, y1, z0), (x1, y0, z0))
            self.tri(side, (x0, y0, z1), (x1, y0, z1), (x1, y1, z1))
            self.wall(side, x1, z0, x1, z1, y0, y1, scale)

    def disc(self, top, side, cx, cz, r, y0, y1, n=16, scale=100.0, tint=(1, 1, 1)):
        """A round platform: top fan and the rim wall."""
        pts = [(cx + r * math.sin(2 * math.pi * k / n), cz + r * math.cos(2 * math.pi * k / n)) for k in range(n)]
        for k in range(n):
            (ax, az), (bx, bz) = pts[k], pts[(k + 1) % n]
            self.tri(top, (cx, y1, cz), (ax, y1, az), (bx, y1, bz), tint=tint,
                     uv=[(cx / scale, cz / scale), (ax / scale, az / scale), (bx / scale, bz / scale)])
            self.wall(side, bx, bz, ax, az, y0, y1, scale, tint)

    def heightfield(self, mat, x0, z0, x1, z1, step, height, scale=100.0, tint_fn=None, skip=None):
        """Ground from a height function (x, z) -> y, in cells of `step`; `skip(x, z)` leaves a cell out (a hole for
        water or lava drawn on its own)."""
        x = x0
        while x < x1:
            z = z0
            while z < z1:
                if not (skip and skip(x + step / 2, z + step / 2)):
                    p = [(x, height(x, z), z), (x, height(x, z + step), z + step),
                         (x + step, height(x + step, z + step), z + step), (x + step, height(x + step, z), z)]
                    tint = tint_fn(x, z) if tint_fn else (1, 1, 1)
                    self.quad(mat, *p, scale=scale, tint=tint)
                z += step
            x += step

    def triangles(self) -> int:
        return sum(len(p[0]) for p in self.parts.values()) // 3


def image(png, kind: str, size: int, seed: int) -> bytes:
    """A small drawn texture: grass, rock, stone (blocks), metal (panels), lava, water, sand, crystal, snow, ice,
    cloud."""
    rng = random.Random(seed)
    noise = {}
    px = bytearray()
    for y in range(size):
        for x in range(size):
            n = noise.setdefault((x // 4, y // 4), rng.randint(-18, 18)) + rng.randint(-6, 6)
            if kind == "grass":
                c = (70 + n, 140 + n, 55 + n // 2, 255)
            elif kind == "rock":
                c = (110 + n, 95 + n, 85 + n, 255)
            elif kind == "stone":
                seam = y % 16 == 0 or (x + (8 if (y // 16) % 2 else 0)) % 16 == 0
                v = 90 if seam else 150 + n
                c = (v, v, v + 8, 255)
            elif kind == "metal":
                seam = x % 32 == 0 or y % 32 == 0
                v = 80 if seam else 140 + n // 2
                c = (v - 10, v, v + 20, 255)
            elif kind == "lava":
                c = (230 + n // 3, 90 + n * 2, 20, 255)
            elif kind == "water":
                c = (40 + n // 2, 110 + n, 210, 160)
            elif kind == "sand":
                c = (205 + n, 180 + n, 120 + n, 255)
            elif kind == "snow":
                c = (225 + n // 2, 232 + n // 2, 245, 255)
            elif kind == "ice":
                streak = (x + y) % 11 == 0
                c = (170 + n, 215 + n // 2, 245, 255) if not streak else (225, 245, 255, 255)
            elif kind == "cloud":
                c = (235 + n // 3, 225 + n // 3, 250, 255)
            else:  # crystal
                c = (150 + n, 90 + n, 220, 255)
            px += bytes(max(0, min(255, v)) for v in c)
    return png.write_png("RGBA", size, size, bytes(px))


def write_glb(mesh: Mesh, out: Path, images: dict, generator: str) -> None:
    """images: image name -> (kind, size, seed)."""
    formats.load()
    from jfgformats import gltf, png
    g = gltf.GlbWriter(generator=generator)
    doc = g.doc
    doc["images"], doc["textures"], doc["materials"] = [], [], []
    doc["samplers"] = [{"magFilter": 9729, "minFilter": 9729, "wrapS": 10497, "wrapT": 10497}]
    tex = {}
    used = sorted({mesh.images[m] for m in mesh.parts})
    for name in used:
        kind, size, seed = images[name]
        doc["images"].append({"name": name, "mimeType": "image/png", "bufferView": g.view(image(png, kind, size,
                                                                                               seed))})
        doc["textures"].append({"source": len(doc["images"]) - 1, "sampler": 0})
        tex[name] = len(doc["textures"]) - 1
    prims = []
    for name, (pos, uvs, cols) in mesh.parts.items():
        kind = mesh.kinds.get(name, "opaque")
        mat = {"name": name, "pbrMetallicRoughness": {"baseColorTexture": {"index": tex[mesh.images[name]]},
                                                     "metallicFactor": 0.0, "roughnessFactor": 1.0},
               "extras": {"batchFlags": FLAGS[kind], "pass": "translucent" if kind == "water" else "opaque"}}
        if kind == "water":
            mat["alphaMode"] = "BLEND"
        doc["materials"].append(mat)
        prims.append({"attributes": {"POSITION": g.accessor("f", pos, 5126, "VEC3", bounds=True),
                                     "TEXCOORD_0": g.accessor("f", uvs, 5126, "VEC2"),
                                     "COLOR_0": g.accessor("B", cols, 5121, "VEC4", normalized=True)},
                      "mode": 4, "material": len(doc["materials"]) - 1})
    doc["meshes"] = [{"name": out.stem, "primitives": prims}]
    doc["nodes"] = [{"name": out.stem, "mesh": 0}]
    doc["scene"], doc["scenes"] = 0, [{"nodes": [0]}]
    out.parent.mkdir(parents=True, exist_ok=True)
    g.write(str(out))
