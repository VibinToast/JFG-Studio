"""The 3D Scene view (task S12/S13): moderngl draws the level into an off-screen texture the Scene window shows.

The track comes from a `.glb` (the project's geometry, or a stock track exported into the local catalog), drawn the
way the game does: vertex colours times the texture, with the level's fog. Water is translucent, lava tinted, kill
planes red, invisible colliders as wireframe. Objects with a model in the user's catalog are drawn with it, in its rest
pose, turned and scaled the way the entry says; the rest (zones, lights, markers the game doesn't draw) are markers: a
post with a head per kind, rings for exit radii, region polygons on the floor, and lines from members to their
squadron and from a squadron to the door it locks.

The camera works like an engine editor's: right mouse to look with WASD / Q E to fly (Shift faster, the wheel sets the
speed), middle mouse to pan, Alt + left to orbit around the selection, F to frame it.
"""

import math
import os
import struct

import moderngl
import numpy as np

from jfgstudio import formats

TRACK_VS = """
#version 330
uniform mat4 mvp; uniform mat4 model;
in vec3 in_pos; in vec2 in_uv; in vec4 in_col;
out vec2 uv; out vec4 col; out float depth;
void main() { gl_Position = mvp * model * vec4(in_pos, 1.0); uv = in_uv; col = in_col; depth = gl_Position.w; }
"""
TRACK_FS = """
#version 330
uniform sampler2D tex; uniform bool textured; uniform vec4 tint; uniform float alpha;
uniform vec3 fog_col; uniform vec2 fog_range; uniform bool fog_on;
in vec2 uv; in vec4 col; in float depth;
out vec4 frag;
void main() {
    vec4 c = col * (textured ? texture(tex, uv) : vec4(1.0));
    c.rgb = mix(c.rgb, tint.rgb, tint.a);
    if (fog_on) { float f = clamp((depth - fog_range.x) / max(fog_range.y - fog_range.x, 1.0), 0.0, 1.0);
                  c.rgb = mix(c.rgb, fog_col, f); }
    if (c.a * alpha < 0.02) discard;
    frag = vec4(c.rgb, c.a * alpha);
}
"""
LINE_VS = """
#version 330
uniform mat4 mvp;
in vec3 in_pos; in vec4 in_col;
out vec4 col;
void main() { gl_Position = mvp * vec4(in_pos, 1.0); col = in_col; }
"""
LINE_FS = """
#version 330
in vec4 col; out vec4 frag;
void main() { frag = col; }
"""

# Marker colours per schema key (RGBA 0-1); anything else is grey.
KIND_COLOURS = {
    "startpoint": (0.24, 0.78, 1.0, 1.0), "exit": (1.0, 0.70, 0.10, 1.0), "squadron": (1.0, 0.29, 0.29, 1.0),
    "squadmember": (1.0, 0.55, 0.55, 1.0), "door": (0.70, 0.45, 1.0, 1.0), "regionpoint": (0.3, 1.0, 0.5, 1.0),
    "light": (1.0, 0.95, 0.45, 1.0), "glow": (1.0, 0.85, 0.6, 1.0), "audio": (0.45, 1.0, 0.75, 1.0),
    "camerazone": (0.45, 0.85, 1.0, 1.0), "fogzone": (1.0, 0.6, 0.9, 1.0), "patrolpoint": (1.0, 0.6, 0.25, 1.0),
    "patrolpath": (1.0, 0.6, 0.25, 1.0), "refuel": (0.6, 0.9, 1.0, 1.0), "animtrigger": (0.85, 0.75, 1.0, 1.0),
}
OTHER_COLOUR = (0.75, 0.80, 0.90, 1.0)
HOLE_COLOUR = (1.0, 0.35, 0.75, 1.0)  # a hole of a region (an obstacle squads take cover behind)
SELECTED_COLOUR = (1.0, 1.0, 0.2, 1.0)
MARKER_HEIGHT = 40.0


def perspective(fov_y: float, aspect: float, near: float, far: float) -> np.ndarray:
    f = 1.0 / math.tan(fov_y / 2)
    return np.array([[f / aspect, 0, 0, 0], [0, f, 0, 0],
                     [0, 0, (far + near) / (near - far), 2 * far * near / (near - far)], [0, 0, -1, 0]], "f4")


class Camera:
    def __init__(self):
        self.pos = np.array([0.0, 300.0, -800.0])
        self.yaw, self.pitch = 0.0, -0.3  # yaw 0 looks along +z
        self.speed = 600.0  # units per second
        self.fov = math.radians(60)

    def forward(self) -> np.ndarray:
        cp = math.cos(self.pitch)
        return np.array([math.sin(self.yaw) * cp, math.sin(self.pitch), math.cos(self.yaw) * cp])

    def right(self) -> np.ndarray:
        return np.array([-math.cos(self.yaw), 0.0, math.sin(self.yaw)])

    def view(self) -> np.ndarray:
        f = self.forward()
        r = np.cross(f, [0.0, 1.0, 0.0])
        r /= np.linalg.norm(r) or 1.0
        u = np.cross(r, f)
        m = np.identity(4, "f4")
        m[0, :3], m[1, :3], m[2, :3] = r, u, -f
        m[:3, 3] = [-r @ self.pos, -u @ self.pos, f @ self.pos]
        return m

    def projection(self, aspect: float) -> np.ndarray:
        return perspective(self.fov, aspect, 5.0, 60000.0)

    def frame(self, point, distance: float = 600.0) -> None:
        self.pos = np.asarray(point, float) - self.forward() * distance

    def look_from_above(self, bounds) -> None:
        lo, hi = bounds
        centre = (lo + hi) / 2
        size = float(max(hi[0] - lo[0], hi[2] - lo[2], 200.0))
        self.yaw, self.pitch = 0.0, -0.6
        self.frame(centre, size * 0.9)


def _node_matrix(node: dict) -> np.ndarray:
    if "matrix" in node:
        return np.array(node["matrix"], "f8").reshape(4, 4).T
    m = np.identity(4)
    if "scale" in node:
        m = np.diag([*node["scale"], 1.0]) @ m
    if "rotation" in node:
        x, y, z, w = node["rotation"]
        r = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                      [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                      [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
        rm = np.identity(4)
        rm[:3, :3] = r
        m = rm @ m
    if "translation" in node:
        t = np.identity(4)
        t[:3, 3] = node["translation"]
        m = t @ m
    return m


class TrackMesh:
    """A .glb as triangles per material: positions, UVs, colours, and what kind of surface each is. Tracks read the
    surface from the material; a model (`surfaces=False`) is solid all over. A skinned model is drawn in its rest
    pose: each vertex through its joints' rest matrices and inverse bind matrices."""

    def __init__(self, path: str, surfaces: bool = True):
        self.surfaces = surfaces
        jf = formats.load()
        from jfgformats import gltf, png
        doc, blob = gltf.read_glb(path)
        self.parts = []  # (positions (n,3), uvs (n,2), colours (n,4), image rgba or None, (w, h), kind)
        self.images = {}
        nodes = doc.get("nodes", [])

        world = {}

        def walk(index, parent):
            world[index] = parent @ _node_matrix(nodes[index])
            for child in nodes[index].get("children", []):
                walk(child, world[index])

        roots = doc.get("scenes", [{}])[doc.get("scene", 0)].get("nodes", list(range(len(nodes)))) if nodes else []
        for r in roots:
            walk(r, np.identity(4))
        for index in sorted(world):
            node = nodes[index]
            if "mesh" not in node:
                continue
            skin = None
            if "skin" in node:
                sk = doc["skins"][node["skin"]]
                joints = sk["joints"]
                inverse = (np.array(gltf.read_accessor(doc, blob, sk["inverseBindMatrices"]), "f8").reshape(-1, 4, 4)
                           .transpose(0, 2, 1) if "inverseBindMatrices" in sk else np.tile(np.identity(4),
                                                                                           (len(joints), 1, 1)))
                skin = np.array([world.get(j, np.identity(4)) @ inverse[k] for k, j in enumerate(joints)])
            self._mesh(doc, blob, doc["meshes"][node["mesh"]], world[index], gltf, png, skin)
        tris = [p[0] for p in self.parts if p[5] not in ("nocollide", "water", "kill")]
        self.collision = np.concatenate(tris).reshape(-1, 3, 3) if tris else np.zeros((0, 3, 3))
        tris = [p[0] for p in self.parts if p[5] != "hidden"]
        self.visible = np.concatenate(tris).reshape(-1, 3, 3) if tris else np.zeros((0, 3, 3))
        allpos = np.concatenate([p[0] for p in self.parts]) if self.parts else np.zeros((1, 3))
        self.bounds = (allpos.min(axis=0), allpos.max(axis=0))
        self.version = jf.__version__

    def _mesh(self, doc, blob, mesh, matrix, gltf, png, skin=None):
        for prim in mesh.get("primitives", []):
            if prim.get("mode", 4) != 4:
                continue
            attrs = prim["attributes"]
            pos = np.array(gltf.read_accessor(doc, blob, attrs["POSITION"]), "f4")
            count = len(pos)
            idx = ([i[0] for i in gltf.read_accessor(doc, blob, prim["indices"])] if "indices" in prim
                   else list(range(count)))
            uv = (np.array(gltf.read_accessor(doc, blob, attrs["TEXCOORD_0"]), "f4") if "TEXCOORD_0" in attrs
                  else np.zeros((count, 2), "f4"))
            if "COLOR_0" in attrs:
                col = np.array(gltf.read_accessor(doc, blob, attrs["COLOR_0"]), "f4")
                if col.shape[1] == 3:
                    col = np.hstack([col, np.ones((count, 1), "f4")])
            else:
                col = np.ones((count, 4), "f4")
            homog = np.hstack([pos, np.ones((count, 1), "f4")])
            if skin is not None and "JOINTS_0" in attrs and "WEIGHTS_0" in attrs:
                joints = np.array(gltf.read_accessor(doc, blob, attrs["JOINTS_0"]), "i4")
                weights = np.array(gltf.read_accessor(doc, blob, attrs["WEIGHTS_0"]), "f8")
                weights /= np.maximum(weights.sum(axis=1, keepdims=True), 1e-9)
                blended = np.einsum("nk,nkij->nij", weights, skin[np.clip(joints, 0, len(skin) - 1)])
                pos = np.einsum("nij,nj->ni", blended, homog)[:, :3].astype("f4")  # a skinned mesh ignores its node
            else:
                pos = (homog @ matrix.T)[:, :3].astype("f4")
            idx = np.array(idx, "i4")
            mat = doc.get("materials", [{}])[prim["material"]] if "material" in prim else {}
            if not self.surfaces:
                col[:, 3] = 1.0  # a model's vertex alpha is not its opacity (many are 0); texture alpha still cuts out
            image, size = None, (1, 1)
            tex = mat.get("pbrMetallicRoughness", {}).get("baseColorTexture")
            if tex is not None:
                key = tex["index"]
                if key not in self.images:
                    try:
                        w, h, rgba = png.read_png(gltf.image_bytes(doc, blob, key))
                        self.images[key] = (rgba, (w, h))
                    except Exception:  # an image Studio can't read draws untextured
                        self.images[key] = (None, (1, 1))
                image, size = self.images[key]
            self.parts.append((pos[idx], uv[idx], col[idx], image, size, _surface(mat) if self.surfaces else "solid"))

    def floor_height(self, x: float, z: float, below: float | None = None) -> float | None:
        """The highest floor under (x, z) (below a height when given): a ray down against the solid triangles."""
        return floor_of(self.collision, x, z, below)

    def kind_triangles(self, kind: str) -> np.ndarray:
        """The triangles (n, 3, 3) of one surface kind (solid, water, lava, kill, hidden, nocollide)."""
        tris = [p[0] for p in self.parts if p[5] == kind]
        return np.concatenate(tris).reshape(-1, 3, 3) if tris else np.zeros((0, 3, 3))


def floor_of(t: np.ndarray, x: float, z: float, below: float | None = None) -> float | None:
    """The highest of the triangles (n, 3, 3) straight under or over (x, z) (at or below `below` when given)."""
    ys = heights_at(t, x, z)
    if below is not None:
        ys = ys[ys <= below + 1.0]
    return float(ys.max()) if len(ys) else None


def heights_at(t: np.ndarray, x: float, z: float, facing: int = 0) -> np.ndarray:
    """The heights of all the triangles (n, 3, 3) straight under or over (x, z); `facing` 1 / -1: only those facing
    up / down (counter-clockwise seen from the front, as glTF and the game's tracks wind them)."""
    if not len(t):
        return np.zeros(0)
    if facing:
        e1, e2 = t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]
        ny = e1[:, 2] * e2[:, 0] - e1[:, 0] * e2[:, 2]  # the y of (b - a) x (c - a)
        t = t[ny * facing > 0]
        if not len(t):
            return np.zeros(0)
    a, b, c = t[:, 0], t[:, 1], t[:, 2]
    # Barycentric test in the xz plane.
    v0x, v0z = c[:, 0] - a[:, 0], c[:, 2] - a[:, 2]
    v1x, v1z = b[:, 0] - a[:, 0], b[:, 2] - a[:, 2]
    v2x, v2z = x - a[:, 0], z - a[:, 2]
    den = v0x * v1z - v1x * v0z
    ok = np.abs(den) > 1e-6
    den = np.where(ok, den, 1.0)
    u = (v2x * v1z - v1x * v2z) / den
    v = (v0x * v2z - v2x * v0z) / den
    inside = ok & (u >= 0) & (v >= 0) & (u + v <= 1)
    y = a[:, 1] + u * (c[:, 1] - a[:, 1]) + v * (b[:, 1] - a[:, 1])
    return y[inside]


def raycast(triangles: np.ndarray, origin, direction) -> float | None:
    """Distance along a ray to the nearest of the triangles (n, 3, 3), or None (Moller-Trumbore, both sides)."""
    if not len(triangles):
        return None
    o, d = np.asarray(origin, "f8"), np.asarray(direction, "f8")
    a = triangles[:, 0].astype("f8")
    e1, e2 = triangles[:, 1] - a, triangles[:, 2] - a
    p = np.cross(d, e2)
    det = np.einsum("ij,ij->i", e1, p)
    ok = np.abs(det) > 1e-9
    inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
    t_vec = o - a
    u = np.einsum("ij,ij->i", t_vec, p) * inv
    q = np.cross(t_vec, e1)
    v = (q @ d) * inv
    t = np.einsum("ij,ij->i", e2, q) * inv
    hit = ok & (u >= 0) & (v >= 0) & (u + v <= 1) & (t > 1e-3)
    return float(t[hit].min()) if hit.any() else None


def _surface(mat: dict) -> str:
    """The game surface a material makes, from the same words and flags tracks.py reads."""
    name = mat.get("name", "").lower()
    flags = (mat.get("extras") or {}).get("batchFlags", 0)
    tri_flags = (mat.get("extras") or {}).get("triangleFlags", 0)
    words = set("".join(ch if ch.isalnum() else " " for ch in name).split())
    if "kill" in words or flags & 0x80000000:
        return "kill"
    if "water" in words or flags & 0x2000:
        return "water"
    if "lava" in words or "damage" in words or flags & 0x8000:
        return "lava"
    if "collider" in words or "hidden" in words or flags & 0x400:
        return "hidden"
    if "nocollide" in words or tri_flags & 0x80:
        return "nocollide"
    if "ledge" in words or tri_flags & 0x02:
        return "ledge"  # solid: a top whose edges the heroes can grab
    return "solid"


class Viewport:
    def __init__(self):
        self.ctx = None
        self.fbo = None
        self.size = (0, 0)
        self.mesh = None
        self.mesh_path = None
        self.mesh_stamp = None
        self.gpu_parts = []
        self.fog = None  # (rgb 0-1, near, far) in world units, or None
        self.show_fog = True
        self.show_hidden = True
        self.show_sky = True
        self.error = ""
        self.sky = None  # (dome model .glb, mode): mode 0 follows the camera, 1 stays at the origin (MSR "Sky")
        self.clear = None  # the level's clear colour (rgb 0-1), shown where there is no sky
        self.models = {}  # .glb path -> TrackMesh (or None when it could not be read)
        self.model_parts = {}  # .glb path -> uploaded parts

    def _init(self):
        self.ctx = moderngl.create_context()
        self.track_prog = self.ctx.program(vertex_shader=TRACK_VS, fragment_shader=TRACK_FS)
        self.line_prog = self.ctx.program(vertex_shader=LINE_VS, fragment_shader=LINE_FS)
        self.white = self.ctx.texture((1, 1), 4, bytes([255, 255, 255, 255]))

    def load_track(self, path: str | None) -> None:
        """Shows a track .glb; it is read again when the file changes (a water plane, a new export)."""
        try:
            stamp = os.stat(path).st_mtime_ns if path else None
        except OSError:
            stamp = None
        if path == self.mesh_path and stamp == self.mesh_stamp:
            return
        self.mesh_path, self.mesh_stamp, self.mesh, self.error = path, stamp, None, ""
        for vao, _, tex, _ in self.gpu_parts:
            vao.release()
            if tex is not None:
                tex.release()
        self.gpu_parts = []
        if not path:
            return
        try:
            self.mesh = TrackMesh(path)
        except Exception as e:  # shown in the Scene window, not raised into the editor
            self.error = f"can't show {path}: {e}"

    def model(self, path: str) -> "TrackMesh | None":
        """A model .glb, read once."""
        if path not in self.models:
            try:
                self.models[path] = TrackMesh(path, surfaces=False)
            except Exception:  # an unreadable model is drawn as a marker
                self.models[path] = None
        return self.models[path]

    def _upload(self, mesh=None):
        textures = {}
        out = [] if mesh is not None else self.gpu_parts
        for pos, uv, col, image, size, kind in (mesh or self.mesh).parts:
            data = np.hstack([pos, uv, col]).astype("f4")
            vbo = self.ctx.buffer(data.tobytes())
            vao = self.ctx.vertex_array(self.track_prog, [(vbo, "3f 2f 4f", "in_pos", "in_uv", "in_col")])
            tex = None
            if image is not None:
                key = id(image)
                if key not in textures:
                    t = self.ctx.texture(size, 4, image)
                    t.repeat_x = t.repeat_y = True
                    t.filter = (moderngl.LINEAR, moderngl.LINEAR)
                    textures[key] = t
                tex = textures[key]
            out.append((vao, len(data), tex, kind))
        return out

    def _draw_sky(self, mvp, camera) -> None:
        """The level's sky dome, behind everything and unfogged: around the camera (mode 0) or at the origin."""
        path, mode = self.sky
        if path not in self.model_parts:
            mesh = self.model(path)
            self.model_parts[path] = self._upload(mesh) if mesh is not None else []
        prog = self.track_prog
        prog["mvp"].write(mvp.T.tobytes())
        m = np.identity(4, "f4")
        if mode == 0:
            m[:3, 3] = camera.pos
        prog["model"].write(m.T.tobytes())
        prog["fog_on"].value = False
        prog["tint"].value = (0.0, 0.0, 0.0, 0.0)
        prog["alpha"].value = 1.0
        self.ctx.disable(moderngl.DEPTH_TEST)
        self.ctx.depth_mask = False
        for vao, count, tex, _ in self.model_parts[path]:
            prog["textured"].value = tex is not None
            (tex or self.white).use(0)
            vao.render(moderngl.TRIANGLES, vertices=count)
        self.ctx.depth_mask = True

    def render(self, width: int, height: int, camera: Camera, lines: np.ndarray, instances=()) -> int | None:
        """Draws into the off-screen texture; returns its GL id, or None when there is nothing to show.
        `instances`: (model .glb path, 4x4 model matrix, tint rgba) for every object drawn with its model."""
        if width < 8 or height < 8:
            return None
        if self.ctx is None:
            self._init()
        if self.mesh is not None and not self.gpu_parts:
            self._upload()
        if self.size != (width, height):
            if self.fbo:
                self.fbo.release()
                self.colour.release()
                self.depth.release()
            self.colour = self.ctx.texture((width, height), 4)
            self.depth = self.ctx.depth_renderbuffer((width, height))
            self.fbo = self.ctx.framebuffer([self.colour], self.depth)
            self.size = (width, height)
        self.fbo.use()
        back = self.fog[0] if self.fog and self.show_fog else self.clear or (0.02, 0.04, 0.11)
        self.fbo.clear(*back, 1.0, depth=1.0)
        mvp = (camera.projection(width / height) @ camera.view()).astype("f4")
        if self.sky:
            self._draw_sky(mvp, camera)
        self.ctx.enable(moderngl.DEPTH_TEST)
        prog = self.track_prog
        prog["mvp"].write(mvp.T.tobytes())
        prog["model"].write(np.identity(4, "f4").tobytes())
        fog_on = bool(self.fog) and self.show_fog
        prog["fog_on"].value = fog_on
        if self.fog:
            prog["fog_col"].value = tuple(self.fog[0])
            prog["fog_range"].value = (float(self.fog[1]), float(self.fog[2]))
        tints = {"lava": (1.0, 0.35, 0.05, 0.45), "kill": (1.0, 0.0, 0.0, 0.6), "water": (0.2, 0.5, 1.0, 0.2)}
        for translucent in (False, True):
            for vao, count, tex, kind in self.gpu_parts:
                if (kind == "water") != translucent:
                    continue
                if kind == "hidden" and not self.show_hidden:
                    continue
                prog["textured"].value = tex is not None
                (tex or self.white).use(0)
                prog["tex"].value = 0
                prog["tint"].value = tints.get(kind, (0.0, 0.0, 0.0, 0.0))
                prog["alpha"].value = 0.55 if kind == "water" else 1.0
                if kind == "water":
                    self.ctx.enable(moderngl.BLEND)
                    self.ctx.depth_mask = False
                if kind == "hidden":
                    self.ctx.wireframe = True
                vao.render(moderngl.TRIANGLES, vertices=count)
                self.ctx.wireframe = False
                self.ctx.depth_mask = True
                self.ctx.disable(moderngl.BLEND)
        prog["alpha"].value = 1.0
        for path, matrix, tint in instances:
            if path not in self.model_parts:
                mesh = self.model(path)
                self.model_parts[path] = self._upload(mesh) if mesh is not None else []
            prog["model"].write(np.asarray(matrix, "f4").T.tobytes())
            prog["tint"].value = tuple(tint)
            for vao, count, tex, _ in self.model_parts[path]:
                prog["textured"].value = tex is not None
                (tex or self.white).use(0)
                vao.render(moderngl.TRIANGLES, vertices=count)
        prog["model"].write(np.identity(4, "f4").tobytes())
        if len(lines):
            self.line_prog["mvp"].write(mvp.T.tobytes())
            vbo = self.ctx.buffer(lines.astype("f4").tobytes())
            vao = self.ctx.vertex_array(self.line_prog, [(vbo, "3f 4f", "in_pos", "in_col")])
            self.ctx.disable(moderngl.DEPTH_TEST)  # markers stay visible through walls, as in an editor
            vao.render(moderngl.LINES)
            vao.release()
            vbo.release()
        self.ctx.disable(moderngl.DEPTH_TEST)
        self.ctx.screen.use()
        return self.colour.glo


def project(camera: Camera, size, point) -> tuple[float, float] | None:
    """Screen position (pixels from the image's top left) of a world point, or None behind the camera."""
    w, h = size
    mvp = camera.projection(w / h) @ camera.view()
    p = mvp @ np.array([*point, 1.0])
    if p[3] <= 1e-3:
        return None
    x, y = p[0] / p[3], p[1] / p[3]
    return ((x + 1) * 0.5 * w, (1 - y) * 0.5 * h)


def marker_lines(objects: list, keys: dict, selection: set, links: list, boxes: dict | None = None,
                 rings: dict | None = None) -> np.ndarray:
    """Line vertices (x, y, z, r, g, b, a) for every object's marker, ring and link. `boxes`: uid -> the 8 world
    corners of the model an object is drawn with; those get no post, and a box when selected. `rings`: uid -> radii of
    the ranges it has (a light's reach, a sound's range, a camera zone's radii), drawn on the floor."""
    out = []
    boxes = boxes or {}
    rings = rings or {}

    def seg(a, b, c):
        out.append((*a, *c))
        out.append((*b, *c))

    for o in objects:
        x, y, z = (float(v) for v in o["pos"])
        key = keys.get(o["uid"])
        col = SELECTED_COLOUR if o["uid"] in selection else KIND_COLOURS.get(key, OTHER_COLOUR)
        for r in rings.get(o["uid"], []):
            if r > 0:
                faint = col if o["uid"] in selection else (col[0], col[1], col[2], 0.45)
                steps = 32
                for i in range(steps):
                    a0, a1 = 2 * math.pi * i / steps, 2 * math.pi * (i + 1) / steps
                    seg((x + math.sin(a0) * r, y + 2, z + math.cos(a0) * r),
                        (x + math.sin(a1) * r, y + 2, z + math.cos(a1) * r), faint)
        if o["uid"] in boxes:
            if o["uid"] in selection:
                c = boxes[o["uid"]]
                for i, j in ((0, 1), (0, 2), (0, 4), (1, 3), (1, 5), (2, 3), (2, 6), (3, 7), (4, 5), (4, 6), (5, 7),
                             (6, 7)):
                    seg(c[i], c[j], col)
            continue
        if key == "regionpoint":
            seg((x, y, z), (x, y + 15, z), col)
            continue
        top = y + MARKER_HEIGHT
        seg((x, y, z), (x, top, z), col)
        for dx, dz in ((12, 0), (0, 12)):
            seg((x - dx, top, z - dz), (x + dx, top, z + dz), col)
        if key == "startpoint":
            # The hero faces -z at yaw 0 (seen in the port, 2026-10-25): the arrow points that way.
            yaw = o.get("fields", {}).get("yaw", 0) * 1024 / 65536 * 2 * math.pi
            seg((x, y + 5, z), (x - math.sin(yaw) * 40, y + 5, z - math.cos(yaw) * 40), col)
        if key == "exit":
            r = o.get("fields", {}).get("radius", 0)
            r = r if r >= 5 else 60
            steps = 24
            for i in range(steps):
                a0, a1 = 2 * math.pi * i / steps, 2 * math.pi * (i + 1) / steps
                seg((x + math.sin(a0) * r, y + 2, z + math.cos(a0) * r),
                    (x + math.sin(a1) * r, y + 2, z + math.cos(a1) * r), col)
    for a, b, c in links:
        seg(a, b, c)
    return np.array(out, "f4").reshape(-1, 7) if out else np.zeros((0, 7), "f4")


def region_links(objects: list, keys: dict) -> list:
    """Region polygons (corner to corner; holes in their own colour, crossed) and links: members to their squadron,
    squadrons to their doors."""
    from jfgstudio.prefabs import hole_regions
    out = []
    holes = hole_regions(objects, lambda o: keys.get(o["uid"]))
    regions = {}
    for o in objects:
        if keys.get(o["uid"]) == "regionpoint":
            f = o.get("fields", {})
            regions.setdefault(f.get("region"), []).append((f.get("corner", 0), o["pos"]))
    for region, corners in regions.items():
        pts = [tuple(float(v) for v in p) for _, p in sorted(corners, key=lambda c: c[0])]
        colour = HOLE_COLOUR if region in holes else KIND_COLOURS["regionpoint"]
        for a, b in zip(pts, pts[1:] + pts[:1], strict=True):
            out.append((a, b, colour))
        if region in holes and len(pts) >= 4:  # crossed, so a hole reads as an obstacle
            half = len(pts) // 2
            out.append((pts[0], pts[half], colour))
            out.append((pts[half // 2], pts[half + (len(pts) - half) // 2], colour))
    nodes = {o.get("fields", {}).get("node"): o for o in objects if keys.get(o["uid"]) == "patrolpoint"}
    for o in objects:
        if keys.get(o["uid"]) == "patrolpath":
            f = o.get("fields", {})
            a, b = nodes.get(f.get("nodeA")), nodes.get(f.get("nodeB"))
            if a and b:
                lift = np.array([0.0, 8.0, 0.0])
                out.append((tuple(np.array(a["pos"], float) + lift), tuple(np.array(b["pos"], float) + lift),
                            KIND_COLOURS["patrolpath"]))
    squads = {o.get("fields", {}).get("squadronId"): o for o in objects if keys.get(o["uid"]) == "squadron"}
    doors = [o for o in objects if keys.get(o["uid"]) == "door"]
    up = np.array([0.0, MARKER_HEIGHT, 0.0])
    for o in objects:
        if keys.get(o["uid"]) == "squadmember":
            s = squads.get(o.get("fields", {}).get("squadron"))
            if s:
                out.append((tuple(np.array(o["pos"], float) + up), tuple(np.array(s["pos"], float) + up),
                            (1.0, 0.55, 0.55, 0.6)))
    for s in squads.values():
        door = s.get("fields", {}).get("doorId", 0)
        for d in doors:
            if door and d.get("fields", {}).get("doorId") == door:
                out.append((tuple(np.array(s["pos"], float) + up), tuple(np.array(d["pos"], float) + up),
                            (0.70, 0.45, 1.0, 0.8)))
    return out


def decode_fog(header: dict):
    """The level's fog for the preview: colour, and a start and end distance. The header's near/far are not world
    units (the game maps them through its depth range), so the preview spreads them over a typical view distance."""
    if not any(header.get(k, 0) for k in ("fogR", "fogG", "fogB", "fogNear", "fogFar")):
        return None
    near, far = header.get("fogNear", 900), header.get("fogFar", 1000)
    span = 6000.0
    start = max(0.0, (near - 900) / 124.0) * span
    end = max(start + 200.0, (far - 900) / 124.0 * span)
    return ((header.get("fogR", 0) / 255, header.get("fogG", 0) / 255, header.get("fogB", 0) / 255), start, end)


def pack_matrix(m: np.ndarray) -> list[float]:
    """A row-major 4x4 numpy matrix as the column-major 16 floats ImGuizmo and OpenGL take."""
    return [float(v) for v in m.T.flatten()]


def unpack_matrix(values) -> np.ndarray:
    return np.array(list(values), "f8").reshape(4, 4).T


def object_matrix(pos, yaw: float = 0.0, pitch: float = 0.0, roll: float = 0.0, scale: float = 1.0) -> np.ndarray:
    """Translation, then yaw (about y), pitch (about x), roll (about z), then a uniform scale."""
    def rot(i, j, a):
        r = np.identity(4)
        c, s = math.cos(a), math.sin(a)
        r[i, i], r[i, j], r[j, i], r[j, j] = c, s, -s, c
        return r
    m = np.identity(4)
    m[:3, 3] = pos
    return m @ rot(0, 2, yaw) @ rot(1, 2, pitch) @ rot(0, 1, roll) @ np.diag([scale, scale, scale, 1.0])


def box_corners(bounds, matrix) -> list[tuple]:
    """The 8 corners of a model's bounds (lo, hi) in world space."""
    lo, hi = bounds
    pts = np.array([[(hi if i & 1 else lo)[0], (hi if i & 2 else lo)[1], (hi if i & 4 else lo)[2], 1.0]
                    for i in range(8)])
    return [tuple(p[:3]) for p in pts @ np.asarray(matrix).T]


def screen_ray(camera: Camera, size, mx: float, my: float):
    """World origin and unit direction of the ray under a pixel of the Scene image."""
    w, h = size
    inv = np.linalg.inv(camera.projection(w / h) @ camera.view())
    ndc = (2 * mx / w - 1, 1 - 2 * my / h)
    near = inv @ np.array([*ndc, -1.0, 1.0])
    far = inv @ np.array([*ndc, 1.0, 1.0])
    a, b = near[:3] / near[3], far[:3] / far[3]
    d = b - a
    return a, d / np.linalg.norm(d)


__all__ = ["Camera", "TrackMesh", "Viewport", "box_corners", "decode_fog", "marker_lines", "object_matrix",
           "pack_matrix", "project", "raycast", "region_links", "screen_ray", "struct", "unpack_matrix"]
