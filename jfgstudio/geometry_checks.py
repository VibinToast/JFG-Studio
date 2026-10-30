"""Checks of a level's own geometry that the game shows only in play (owner's play-tests of agent-built levels).
Falling through long thin floor triangles is the game's poly list filling up (decomp MSR 15): jfgformats
tracks.poly_list_need measures it, and the build sizes each level's polyList to fit (build.py).

- coplanar: faces in one plane that overlap (either facing): they flicker (z-fighting): two blocks' walls in one
  plane, a decal's front and back in one place.
- water under floor: a water surface just under solid floor: the hero sank through the sand over a tide pool
  whose water reached 8 under it.
- open colliders: an invisible (collider) wall with floor or water going on past it at its foot: the player sees
  open ground or sea and walks into nothing (play-tested in open water). End it at a barrier the player can see.

Each returns the places it found ([x, y, z], rounded), numpy only.
"""

import math

import numpy as np

COPLANAR_NORMAL = 0.999  # normals this close (either way) count as one plane
COPLANAR_DISTANCE = 0.5  # and their planes this close
WATER_UNDER = 30  # solid floor this far over a water surface: the hero sank through sand 8 over a pool's water
#                   (play-tested); decks 40 over the sea are walked on


def _normals(t: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    n = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
    length = np.linalg.norm(n, axis=1)
    return n / np.maximum(length, 1e-9)[:, None], length


def _plane_key(n, d):
    """One key for a plane whichever way its faces point: the normal turned so its first non-zero part is positive."""
    if abs(n[0]) > 1e-6:
        s = math.copysign(1.0, n[0])
    elif abs(n[1]) > 1e-6:
        s = math.copysign(1.0, n[1])
    else:
        s = math.copysign(1.0, n[2])
    return tuple(np.round(n * s, 2)), round(d * s / (2 * COPLANAR_DISTANCE))


def _sat(a: np.ndarray, b: np.ndarray) -> bool:
    """Two 2D triangles (3, 2) overlap with some area (touching edges don't count: both were shrunk first)."""
    for tri in (a, b):
        for k in range(3):
            e = tri[(k + 1) % 3] - tri[k]
            axis = np.array([-e[1], e[0]])
            pa, pb = a @ axis, b @ axis
            if pa.max() <= pb.min() or pb.max() <= pa.min():
                return False
    return True


def coplanar(visible, limit: int = 20) -> list[list[int]]:
    """Places where two drawn triangles lie in one plane (either facing) and overlap: z-fighting. Up to `limit`."""
    t = np.asarray(visible, np.float64).reshape(-1, 3, 3)
    if len(t) < 2:
        return []
    n, area = _normals(t)
    keep = area > 1e-3
    t, n = t[keep], n[keep]
    d = np.einsum("ij,ij->i", n, t[:, 0])
    groups: dict = {}
    for i in range(len(t)):
        groups.setdefault(_plane_key(n[i], d[i]), []).append(i)
    out = []
    for (normal, _), idx in groups.items():
        if len(idx) < 2:
            continue
        axis = int(np.argmax(np.abs(normal)))
        dims = [k for k in range(3) if k != axis]
        flat = t[idx][:, :, dims]
        centre = flat.mean(axis=1, keepdims=True)
        flat = centre + (flat - centre) * 0.98  # shrunk: neighbours sharing an edge no longer touch
        lo, hi = flat.min(axis=1), flat.max(axis=1)
        order = np.argsort(lo[:, 0])
        for p, i in enumerate(order):
            for j in order[p + 1:]:
                if lo[j, 0] >= hi[i, 0]:
                    break
                if lo[j, 1] >= hi[i, 1] or lo[i, 1] >= hi[j, 1]:
                    continue
                if _sat(flat[i], flat[j]):
                    out.append([round(float(c)) for c in t[idx[i]].mean(axis=0)])
                    if len(out) >= limit:
                        return out
                    break
    return out


def water_under_floor(collision, water) -> list[list[int]]:
    """Water surfaces with solid floor up to WATER_UNDER over them: where the hero sinks through that floor."""
    from jfgstudio.gui.viewport import heights_at
    w = np.asarray(water, np.float64).reshape(-1, 3, 3)
    solid = np.asarray(collision, np.float64).reshape(-1, 3, 3)
    out = []
    for tri in w:
        c = tri.mean(axis=0)
        for px, py, pz in [c, *(v + (c - v) * 0.2 for v in tri)]:
            h = heights_at(solid, float(px), float(pz), facing=1)
            if ((h > py + 0.5) & (h <= py + WATER_UNDER)).any():
                out.append([round(float(px)), round(float(py)), round(float(pz))])
                break
    return out


OPEN_PROBE = 80  # how far either side of a collider wall to look for floor or water at its foot
OPEN_STEP = 60  # and how far over or under its foot that floor may be


def open_colliders(hidden, solid, water, limit: int = 20) -> list[list[int]]:
    """Places on invisible (collider) walls where the floor or water the player sees goes on at the wall's foot on
    both sides: a wall in the open. Up to `limit`."""
    from jfgstudio.gui.viewport import heights_at
    h = np.asarray(hidden, np.float64).reshape(-1, 3, 3)
    if not len(h):
        return []
    s = np.asarray(solid, np.float64).reshape(-1, 3, 3)
    sn, _ = _normals(s) if len(s) else (np.zeros((0, 3)), None)
    floors = s[sn[:, 1] > 0.7] if len(s) else s
    walk = np.concatenate([floors, np.asarray(water, np.float64).reshape(-1, 3, 3)])
    n, area = _normals(h)
    out = []
    for tri, normal, a in zip(h, n, area, strict=True):
        if a < 1e-3 or abs(normal[1]) > 0.3:
            continue  # a collider floor or roof, not a wall
        side = np.array([normal[0], 0.0, normal[2]])
        side /= max(np.linalg.norm(side), 1e-9)
        base = float(tri[:, 1].min())
        lo = tri[np.argsort(tri[:, 1])[:2]]  # its foot: the two lowest corners
        for f in (0.25, 0.5, 0.75):
            p = lo[0] + (lo[1] - lo[0]) * f
            both = True
            for sign in (1, -1):
                q = p + side * sign * OPEN_PROBE
                ys = heights_at(walk, float(q[0]), float(q[2]))
                if not ((ys > base - OPEN_STEP) & (ys < base + OPEN_STEP)).any():
                    both = False
                    break
            if both:
                out.append([round(float(c)) for c in p])
                break
        if len(out) >= limit:
            break
    return out
