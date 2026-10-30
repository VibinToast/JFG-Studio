"""Checks of a level's own geometry that the game shows only in play (owner's play-tests of agent-built levels):

- slivers: floor triangles across a segment split. The importer puts each triangle in one segment by its centre
  (jfgformats tracks.new_world_segments), and the hero fell through sand made of slivers up to 1000 long, then
  stuck "falling" on a dune. Floors in tiles of 300 at most rarely cross a split by much.
- coplanar: faces in one plane that overlap (either facing): they flicker (z-fighting): two blocks' walls in one
  plane, a decal's front and back in one place.
- water under floor: a water surface just under solid floor: the hero sank through the sand over a tide pool
  whose water reached 8 under it.

Each returns the places it found ([x, y, z], rounded), numpy only.
"""

import math

import numpy as np

SLIVER_REACH = 250  # a floor triangle reaching this far past a segment split, on the side away from its centre: the
#                    hero fell through slivers reaching 600+ (play-tested); tiles of 300 reach 200 at most and played
COPLANAR_NORMAL = 0.999  # normals this close (either way) count as one plane
COPLANAR_DISTANCE = 0.5  # and their planes this close
WATER_UNDER = 30  # solid floor this far over a water surface: the hero sank through sand 8 over a pool's water
#                   (play-tested); decks 40 over the sea are walked on


def _normals(t: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    n = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
    length = np.linalg.norm(n, axis=1)
    return n / np.maximum(length, 1e-9)[:, None], length


def slivers(collision, everything=None) -> list[list[int]]:
    """Floor triangles that reach across one of the importer's segment splits (jfgformats new_world_segments, run on
    `everything`: the level's triangles, the collision when left out) more than SLIVER_REACH past it, on the side
    away from their centre: the game tests a floor only in the segment holding its centre, so past the split the
    hero falls through it. Their centres."""
    t = np.asarray(collision, np.float64).reshape(-1, 3, 3)
    if not len(t):
        return []
    n, area = _normals(t)
    floors = t[(n[:, 1] > 0.7) & (area > 1e-6)]
    nodes = _splits(np.asarray(everything if everything is not None else collision, np.float64).reshape(-1, 3, 3))
    if not nodes or not len(floors):
        return []
    out = []
    for tri in floors:
        c = tri.mean(axis=0)
        node = 0
        while node >= 0:
            left, right, axis, _, value = nodes[node]
            reach = value - tri[:, axis].min() if c[axis] >= value else tri[:, axis].max() - value
            if reach > SLIVER_REACH:
                out.append([round(float(v)) for v in c])
                break
            node = left if c[axis] < value else right
    return out


def _splits(tris: np.ndarray) -> list | None:
    """The importer's BSP split nodes [(left, right, axis, first right segment, value)] for these triangles, from
    jfgformats tracks.new_world_segments (each triangle alone: a ledge's units barely move the splits)."""
    from jfgstudio import formats
    try:
        formats.load()
        from jfgformats import tracks
    except Exception:  # noqa: BLE001 (no jfgformats: no check)
        return None
    groups = [({"triFlags": 0}, [(tuple(v),) for v in tri], [(0, 1, 2)], None) for tri in tris.tolist()]
    return tracks.new_world_segments(groups)[1]


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
