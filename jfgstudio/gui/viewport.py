"""The 3D Scene view (task S12/S13): moderngl draws the level into an off-screen texture the Scene window shows.

The track comes from a `.glb` (the project's geometry, or a stock track exported into the local catalog), drawn the
way the game does: vertex colours times the texture, with the level's fog. Water is translucent, lava tinted, kill
planes red, invisible colliders as wireframe. Objects are markers (no stock models yet: phase 2): a post with a head
per kind, rings for exit radii, region polygons on the floor, and lines from members to their squadron and from a
squadron to the door it locks.

The camera works like an engine editor's: right mouse to look with WASD / Q E to fly (Shift faster, the wheel sets the
speed), middle mouse to pan, Alt + left to orbit around the selection, F to frame it.
"""

import math
import struct

import moderngl
import numpy as np

from jfgstudio import formats

TRACK_VS = """
#version 330
uniform mat4 mvp;
in vec3 in_pos; in vec2 in_uv; in vec4 in_col;
out vec2 uv; out vec4 col; out float depth;
void main() { gl_Position = mvp * vec4(in_pos, 1.0); uv = in_uv; col = in_col; depth = gl_Position.w; }
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
}
OTHER_COLOUR = (0.75, 0.80, 0.90, 1.0)
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
    """A track .glb as triangles per material: positions, UVs, colours, and what kind of surface each is."""

    def __init__(self, path: str):
        jf = formats.load()
        from jfgformats import gltf, png
        doc, blob = gltf.read_glb(path)
        self.parts = []  # (positions (n,3), uvs (n,2), colours (n,4), image rgba or None, (w, h), kind)
        self.images = {}
        nodes = doc.get("nodes", [])

        def walk(index, parent):
            node = nodes[index]
            m = parent @ _node_matrix(node)
            if "mesh" in node:
                self._mesh(doc, blob, doc["meshes"][node["mesh"]], m, gltf, png)
            for child in node.get("children", []):
                walk(child, m)

        roots = doc.get("scenes", [{}])[doc.get("scene", 0)].get("nodes", list(range(len(nodes)))) if nodes else []
        for r in roots:
            walk(r, np.identity(4))
        tris = [p[0] for p in self.parts if p[5] not in ("nocollide", "water", "kill")]
        self.collision = np.concatenate(tris).reshape(-1, 3, 3) if tris else np.zeros((0, 3, 3))
        allpos = np.concatenate([p[0] for p in self.parts]) if self.parts else np.zeros((1, 3))
        self.bounds = (allpos.min(axis=0), allpos.max(axis=0))
        self.version = jf.__version__

    def _mesh(self, doc, blob, mesh, matrix, gltf, png):
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
            homog = np.hstack([pos, np.ones((count, 1), "f4")]) @ matrix.T
            pos = homog[:, :3].astype("f4")
            idx = np.array(idx, "i4")
            mat = doc.get("materials", [{}])[prim["material"]] if "material" in prim else {}
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
            self.parts.append((pos[idx], uv[idx], col[idx], image, size, _surface(mat)))

    def floor_height(self, x: float, z: float, below: float | None = None) -> float | None:
        """The highest floor under (x, z) (below a height when given): a ray down against the solid triangles."""
        t = self.collision
        if not len(t):
            return None
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
        if not inside.any():
            return None
        y = a[:, 1] + u * (c[:, 1] - a[:, 1]) + v * (b[:, 1] - a[:, 1])
        ys = y[inside]
        if below is not None:
            ys = ys[ys <= below + 1.0]
        return float(ys.max()) if len(ys) else None


def _surface(mat: dict) -> str:
    """The game surface a material makes, from the same words and flags tracks.py reads."""
    name = mat.get("name", "").lower()
    flags = (mat.get("extras") or {}).get("batchFlags", 0)
    words = set("".join(ch if ch.isalnum() else " " for ch in name).split())
    if "kill" in words or flags & 0x80000000:
        return "kill"
    if "water" in words or flags & 0x2000:
        return "water"
    if "lava" in words or "damage" in words or flags & 0x8000:
        return "lava"
    if "collider" in words or "hidden" in words or flags & 0x400:
        return "hidden"
    if "nocollide" in words:
        return "nocollide"
    return "solid"


class Viewport:
    def __init__(self):
        self.ctx = None
        self.fbo = None
        self.size = (0, 0)
        self.mesh = None
        self.mesh_path = None
        self.gpu_parts = []
        self.fog = None  # (rgb 0-1, near, far) in world units, or None
        self.show_fog = True
        self.show_hidden = True
        self.error = ""

    def _init(self):
        self.ctx = moderngl.create_context()
        self.track_prog = self.ctx.program(vertex_shader=TRACK_VS, fragment_shader=TRACK_FS)
        self.line_prog = self.ctx.program(vertex_shader=LINE_VS, fragment_shader=LINE_FS)
        self.white = self.ctx.texture((1, 1), 4, bytes([255, 255, 255, 255]))

    def load_track(self, path: str | None) -> None:
        if path == self.mesh_path:
            return
        self.mesh_path, self.mesh, self.error = path, None, ""
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

    def _upload(self):
        textures = {}
        for pos, uv, col, image, size, kind in self.mesh.parts:
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
            self.gpu_parts.append((vao, len(data), tex, kind))

    def render(self, width: int, height: int, camera: Camera, lines: np.ndarray) -> int | None:
        """Draws into the off-screen texture; returns its GL id, or None when there is nothing to show."""
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
        sky = self.fog[0] if self.fog else (0.02, 0.04, 0.11)
        self.fbo.clear(*sky, 1.0, depth=1.0)
        mvp = (camera.projection(width / height) @ camera.view()).astype("f4")
        self.ctx.enable(moderngl.DEPTH_TEST)
        prog = self.track_prog
        prog["mvp"].write(mvp.T.tobytes())
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


def marker_lines(objects: list, keys: dict, selection: set, links: list) -> np.ndarray:
    """Line vertices (x, y, z, r, g, b, a) for every object's marker, ring and link."""
    out = []

    def seg(a, b, c):
        out.append((*a, *c))
        out.append((*b, *c))

    for o in objects:
        x, y, z = (float(v) for v in o["pos"])
        key = keys.get(o["uid"])
        col = SELECTED_COLOUR if o["uid"] in selection else KIND_COLOURS.get(key, OTHER_COLOUR)
        if key == "regionpoint":
            seg((x, y, z), (x, y + 15, z), col)
            continue
        top = y + MARKER_HEIGHT
        seg((x, y, z), (x, top, z), col)
        for dx, dz in ((12, 0), (0, 12)):
            seg((x - dx, top, z - dz), (x + dx, top, z + dz), col)
        if key == "startpoint":
            yaw = o.get("fields", {}).get("yaw", 0) * 1024 / 65536 * 2 * math.pi
            seg((x, y + 5, z), (x + math.sin(yaw) * 40, y + 5, z + math.cos(yaw) * 40), col)
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
    """Region polygons (corner to corner) and links: members to their squadron, squadrons to their doors."""
    out = []
    regions = {}
    for o in objects:
        if keys.get(o["uid"]) == "regionpoint":
            f = o.get("fields", {})
            regions.setdefault(f.get("region"), []).append((f.get("corner", 0), o["pos"]))
    for corners in regions.values():
        pts = [tuple(float(v) for v in p) for _, p in sorted(corners)]
        for a, b in zip(pts, pts[1:] + pts[:1], strict=True):
            out.append((a, b, KIND_COLOURS["regionpoint"]))
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


def object_matrix(pos, yaw: float = 0.0) -> np.ndarray:
    m = np.identity(4)
    c, s = math.cos(yaw), math.sin(yaw)
    m[0, 0], m[0, 2], m[2, 0], m[2, 2] = c, s, -s, c
    m[:3, 3] = pos
    return m


__all__ = ["Camera", "TrackMesh", "Viewport", "decode_fog", "marker_lines", "object_matrix", "pack_matrix",
           "project", "region_links", "struct", "unpack_matrix"]
