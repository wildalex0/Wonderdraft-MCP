"""Sampling helpers for placement ops: polygon / ribbon Poisson-disk (dart throwing)."""
from __future__ import annotations

import math

import numpy as np

MAX_COUNT = 20000


def polygon_area(poly: np.ndarray) -> float:
    x, y = poly[:, 0], poly[:, 1]
    return float(abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))) / 2.0)


def points_in_polygon(poly: np.ndarray, pts: np.ndarray) -> np.ndarray:
    """Even-odd rule, vectorised. ``pts`` is (n, 2); returns bool (n,)."""
    x, y = pts[:, 0], pts[:, 1]
    inside = np.zeros(len(pts), dtype=bool)
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if y1 == y2:
            continue
        cond = (y1 > y) != (y2 > y)
        xi = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
        inside ^= cond & (x < xi)
    return inside


def dart_throw(candidates: np.ndarray, count: int, min_spacing: float) -> np.ndarray:
    """Greedy Poisson-disk selection from pre-drawn candidates (grid accelerated)."""
    if count <= 0 or len(candidates) == 0:
        return np.zeros((0, 2))
    out: list[tuple[float, float]] = []
    cell = max(min_spacing, 1e-6) / math.sqrt(2.0)
    grid: dict[tuple[int, int], tuple[float, float]] = {}
    s2 = min_spacing * min_spacing
    for x, y in candidates:
        cx, cy = int(x // cell), int(y // cell)
        ok = True
        for gx in range(cx - 2, cx + 3):
            for gy in range(cy - 2, cy + 3):
                q = grid.get((gx, gy))
                if q is not None and (q[0] - x) ** 2 + (q[1] - y) ** 2 < s2:
                    ok = False
                    break
            if not ok:
                break
        if ok:
            grid[(cx, cy)] = (float(x), float(y))
            out.append((float(x), float(y)))
            if len(out) >= count:
                break
    return np.asarray(out, dtype=np.float64).reshape(-1, 2)


def _target(density: float, area: float) -> int:
    count = min(MAX_COUNT, int(round(density * area / 10000.0)))
    if density > 0 and area > 0:
        count = max(count, 1)
    return count


def sample_polygon(rng: np.random.Generator, poly: np.ndarray, density: float,
                   min_spacing: float) -> np.ndarray:
    """Poisson-disk points inside ``poly`` (not clipped to the map).

    ``density`` is the target number of points per 100x100 px (10,000 px^2); the
    spacing constraint may make the actual count lower.
    """
    count = _target(density, polygon_area(poly))
    if count <= 0:
        return np.zeros((0, 2))
    lo, hi = poly.min(axis=0), poly.max(axis=0)
    n_try = min(count * 30 + 100, 600000)
    cand = lo + rng.random((n_try, 2)) * (hi - lo)
    cand = cand[points_in_polygon(poly, cand)]
    return dart_throw(cand, count, min_spacing)


def sample_ribbon(rng: np.random.Generator, line: np.ndarray, width: float,
                  density: float, min_spacing: float) -> np.ndarray:
    """Poisson-disk points within ``width/2`` of a polyline (same density units)."""
    seg = np.diff(line, axis=0)
    lens = np.hypot(seg[:, 0], seg[:, 1])
    total = float(lens.sum())
    if total <= 0 or width <= 0:
        return np.zeros((0, 2))
    count = _target(density, total * width)
    if count <= 0:
        return np.zeros((0, 2))
    n_try = min(count * 30 + 100, 600000)
    which = rng.choice(len(lens), size=n_try, p=lens / total)
    t = rng.random(n_try)
    base = line[which] + seg[which] * t[:, None]
    unit = seg[which] / np.maximum(lens[which], 1e-9)[:, None]
    normal = np.stack([-unit[:, 1], unit[:, 0]], axis=1)
    cand = base + normal * ((rng.random(n_try) - 0.5) * width)[:, None]
    return dart_throw(cand, count, min_spacing)
