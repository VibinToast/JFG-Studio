"""How a level is laid out, measured from its collision: a floor map and the numbers that describe its design.

For agents learning the game's level design (stock_level_layout) and checking their own: walkable area, footprint,
how much is roofed (tunnels, indoors), how far the floors rise and in how many tiers, and how the floor splits into
narrow passages (corridors, tunnels, paths), rooms and open ground. Numpy only.
"""

from __future__ import annotations

import struct
import zlib

import numpy as np

NARROW, ROOM = 200, 500  # half widths: up to 400 wide is a passage, up to 1000 a room, wider is open ground


def _raster(tris: np.ndarray, res: float, x0: float, z0: float, nx: int, nz: int, lowest: bool) -> np.ndarray:
    """Per cell, the lowest (or highest) of the triangles' heights over it; NaN where there are none."""
    out = np.full((nz, nx), np.nan)
    pick = np.fmin if lowest else np.fmax
    for a, b, c in tris:
        xs, zs = (a[0], b[0], c[0]), (a[2], b[2], c[2])
        i0, i1 = max(int((min(xs) - x0) // res), 0), min(int((max(xs) - x0) // res) + 1, nx)
        j0, j1 = max(int((min(zs) - z0) // res), 0), min(int((max(zs) - z0) // res) + 1, nz)
        if i0 >= i1 or j0 >= j1:
            continue
        gx, gz = np.meshgrid(x0 + (np.arange(i0, i1) + 0.5) * res, z0 + (np.arange(j0, j1) + 0.5) * res)
        v0, v1 = c - a, b - a
        den = v0[0] * v1[2] - v1[0] * v0[2]
        if abs(den) < 1e-6:
            continue
        px, pz = gx - a[0], gz - a[2]
        u = (px * v1[2] - v1[0] * pz) / den
        v = (v0[0] * pz - px * v0[2]) / den
        y = np.where((u >= -0.01) & (v >= -0.01) & (u + v <= 1.01), a[1] + u * v0[1] + v * v1[1], np.nan)
        out[j0:j1, i0:i1] = pick(out[j0:j1, i0:i1], y)
    return out


def _distance(mask: np.ndarray) -> np.ndarray:
    """Cells to the nearest cell off the mask (how many times the floor can shrink by a cell before the spot goes)."""
    d = np.zeros(mask.shape)
    cur = mask.copy()
    k = 0
    while cur.any():
        k += 1
        d[cur] = k
        nxt = cur.copy()
        nxt[1:] &= cur[:-1]
        nxt[:-1] &= cur[1:]
        nxt[:, 1:] &= cur[:, :-1]
        nxt[:, :-1] &= cur[:, 1:]
        cur = nxt
    return d


def _fits(dt: np.ndarray, radius: float, res: float) -> np.ndarray:
    """The cells a circle of `radius` covers somewhere it fits on the floor (a morphological opening)."""
    centres = dt * res >= radius
    r = max(1, int(radius / res))
    out = centres.copy()
    for dj in range(-r, r + 1):
        for di in range(-r, r + 1):
            if di * di + dj * dj <= r * r:
                out |= np.roll(np.roll(centres, dj, axis=0), di, axis=1)
    return out


def _reached(mask: np.ndarray, seeds: list[tuple[int, int]]) -> np.ndarray:
    """The cells of `mask` joined (8 ways) to any seed cell."""
    out = np.zeros_like(mask)
    for i, j in seeds:
        if 0 <= i < mask.shape[0] and 0 <= j < mask.shape[1] and mask[i, j]:
            out[i, j] = True
    while True:
        grown = out.copy()
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                grown |= np.roll(np.roll(out, di, 0), dj, 1)
        grown &= mask
        if (grown == out).all():
            return out
        out = grown


def study(tris: np.ndarray, starts=None) -> tuple[dict, bytes] | None:
    """(numbers, floor map PNG) of a level's collision triangles (n, 3, 3), Y up. The map: the lowest floor of each
    spot from above (up is -z), brighter = higher, darker where roofed, orange where it is a narrow passage. With
    `starts` ([(x, z)]: the level's start points), only the floor joined to them counts: backdrop geometry outside
    the play area (distant piers, a far shore) is left out ("backdrop": its area)."""
    tris = np.asarray(tris, np.float64)
    if len(tris) < 2:
        return None
    n = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    ln = np.linalg.norm(n, axis=1)
    keep = ln > 1e-6
    tris, ny = tris[keep], n[keep, 1] / ln[keep]
    floors, ceilings = tris[ny > 0.7], tris[ny < -0.7]
    if len(floors) < 2:
        return None
    lo, hi = floors.reshape(-1, 3).min(0), floors.reshape(-1, 3).max(0)
    span = max(hi[0] - lo[0], hi[2] - lo[2])
    res = 25.0 if span < 3000 else 50.0 if span < 8000 else 100.0
    nx, nz = int((hi[0] - lo[0]) / res) + 2, int((hi[2] - lo[2]) / res) + 2
    low = _raster(floors, res, lo[0], lo[2], nx, nz, True)
    ceil = _raster(ceilings, res, lo[0], lo[2], nx, nz, True) if len(ceilings) else np.full_like(low, np.nan)
    mask = ~np.isnan(low)
    backdrop = 0
    if starts:
        reached = _reached(mask, [(int((z - lo[2]) / res), int((x - lo[0]) / res)) for x, z in starts])
        if reached.any():
            backdrop = int(mask.sum() - reached.sum())
            mask = reached
            low[~mask] = np.nan
            ys, xs = np.nonzero(mask)  # the footprint of what is left
            lo, hi = lo.copy(), hi.copy()
            lo[0], hi[0] = lo[0] + xs.min() * res, lo[0] + (xs.max() + 1) * res
            lo[2], hi[2] = lo[2] + ys.min() * res, lo[2] + (ys.max() + 1) * res
    with np.errstate(invalid="ignore"):
        roofed = mask & (ceil > low + 40) & (ceil < low + 1500)
    dt = _distance(mask)  # in cells
    fits_room, fits_open = _fits(dt, NARROW, res) & mask, _fits(dt, ROOM, res) & mask
    narrow = mask & ~fits_room
    room = fits_room & ~fits_open
    cells = max(1, int(mask.sum()))
    # a passage's centre line: its cells as far from the edges as their neighbours across
    ridge = narrow.copy()
    for shift, axis in ((1, 0), (-1, 0), (1, 1), (-1, 1)):
        ridge &= dt >= np.roll(dt, shift, axis)
    h = low[mask]
    hist, _ = np.histogram(h, bins=np.arange(np.floor(h.min() / 50) * 50, h.max() + 100, 50))
    with np.errstate(invalid="ignore"):
        clear = (ceil - low)[roofed]
    stats = {
        "footprint": [round(float(hi[0] - lo[0])), round(float(hi[2] - lo[2]))],
        "walkable_area": round(float(cells * res * res)),
        "roofed": round(float(roofed.sum() / cells), 2),
        "floor_heights": [round(float(np.percentile(h, 5))), round(float(np.percentile(h, 95)))],
        "tiers": int((hist / hist.sum() > 0.04).sum()),
        "passages": round(float(narrow.sum() / cells), 2), "rooms": round(float(room.sum() / cells), 2),
        "open": round(float(1 - (narrow.sum() + room.sum()) / cells), 2),
        "passage_width": round(float(np.median(2 * dt[ridge] * res))) if ridge.any() else None,
        "clearance": round(float(np.median(clear))) if len(clear) else None,
    }
    if backdrop:
        stats["backdrop"] = round(float(backdrop * res * res))
    norm = (low - np.nanmin(low)) / max(float(np.nanmax(low) - np.nanmin(low)), 1.0)
    img = np.zeros(mask.shape + (3,))
    img[mask] = np.stack([0.25 + 0.6 * norm, 0.35 + 0.45 * norm, 0.3 + 0.15 * (1 - norm)], -1)[mask]
    img[roofed] *= 0.6
    img[narrow] = img[narrow] * 0.5 + np.array([0.9, 0.55, 0.2]) * 0.5
    img = (np.clip(img, 0, 1) * 255).astype(np.uint8)
    scale = max(1, int(512 / max(nx, nz)))
    img = np.repeat(np.repeat(img, scale, axis=0), scale, axis=1)
    return stats, _png(img)


def _png(img: np.ndarray) -> bytes:
    h, w, _ = img.shape
    raw = b"".join(b"\0" + img[y].tobytes() for y in range(h))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))
