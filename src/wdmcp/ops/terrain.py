"""Terrain painting ops: land, landmass generation, lakes, rivers, ground paint.

Conventions (see docs/format.md "Spike B" and wdmcp.model.raster_spec):
* ``mask`` alpha is a height field (land >= 128); mask R holds freshwater coverage.
* Polygons / polylines are lists of (x, y) map pixels, origin top-left. Everything
  is clipped to the map; out-of-map or degenerate input never raises (returns 0).
* Every op returns the number of pixels whose RGBA changed in any raster it touched.
* Determinism: all randomness comes from ``numpy.random.default_rng(seed)``.
"""
from __future__ import annotations

import math
from typing import Literal, Sequence

import numpy as np
from PIL import Image, ImageDraw

from wdmcp.model import raster_spec as rs
from wdmcp.model.document import MapDocument
from wdmcp.ops.noise import fbm

Point = tuple[float, float]
_SS = 4  # supersampling factor for anti-aliased coverage
PERSIST = 0.6  # fbm persistence for outlines (0.5 alone is too smooth to read as a coast)
AMPF = 0.5


# ---------------------------------------------------------------- geometry helpers
def _clean(points: Sequence[Point], doc: MapDocument, min_pts: int) -> np.ndarray | None:
    """Finite points as (n, 2) float64, clamped to a generous box around the map."""
    try:
        a = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    except (ValueError, TypeError):
        return None
    a = a[np.isfinite(a).all(axis=1)]
    if len(a) < min_pts:
        return None
    m = max(doc.width, doc.height)
    return np.clip(a, -3 * m, 4 * m)


def _loop_noise(n: int, rng: np.random.Generator, wavelength: float = 3.0) -> np.ndarray:
    """Periodic 1D fractal noise of n samples (n points ~1 px apart), roughly in [-1, 1].

    Octave k has k cells around the loop, starting at 8 (base wavelength = 1/8 of the
    outline) and doubling while the wavelength stays >= ``wavelength`` samples (max 8
    octaves). Persistence 0.5.
    """
    t = np.arange(n, dtype=np.float64) / n
    out = np.zeros(n)
    amp, norm, k = 1.0, 0.0, 8
    for _ in range(8):
        vals = rng.random(k) * 2 - 1
        x = t * k
        i0 = np.floor(x).astype(int)
        f = x - i0
        f = f * f * (3 - 2 * f)
        out += amp * (vals[i0 % k] * (1 - f) + vals[(i0 + 1) % k] * f)
        norm += amp
        amp *= PERSIST
        k *= 2
        if n / k < wavelength:
            break
    return out / norm


def _smooth_loop(a: np.ndarray, win: int) -> np.ndarray:
    out = np.zeros_like(a)
    for s in range(-win, win + 1):
        out += np.roll(a, s, axis=0)
    return out / (2 * win + 1)


def _displace(pts: np.ndarray, roughness: float, rng: np.random.Generator) -> np.ndarray:
    """Densify the outline (~1.5 px steps) and push vertices along the outward normal
    by multi-octave 1D fbm. Amplitude = roughness * 0.5 * sqrt(area), capped; normals are
    smoothed over a short window so tight corners do not explode into loops."""
    if roughness <= 0:
        return pts
    closed = np.vstack([pts, pts[:1]])
    seg = np.hypot(*np.diff(closed, axis=0).T)
    perim = float(seg.sum())
    if perim < 1e-6:
        return pts
    step = max(1.5, perim / 4000.0)
    dense = []
    for (x0, y0), (x1, y1), length in zip(closed[:-1], closed[1:], seg):
        n = max(1, int(math.ceil(length / step)))
        t = np.arange(n) / n
        dense.append(np.column_stack([x0 + (x1 - x0) * t, y0 + (y1 - y0) * t]))
    d = np.vstack(dense)
    x, y = d[:, 0], d[:, 1]
    area2 = float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y))
    tang = np.roll(d, -2, axis=0) - np.roll(d, 2, axis=0)
    tang /= np.maximum(np.hypot(*tang.T), 1e-9)[:, None]
    normal = np.column_stack([tang[:, 1], -tang[:, 0]])
    if area2 < 0:
        normal = -normal  # make it point outward for either winding
    normal = _smooth_loop(normal, 3)
    size = math.sqrt(abs(area2) / 2.0)
    if size < 1e-6:
        size = float(min(np.ptp(pts[:, 0]), np.ptp(pts[:, 1])))
    amp = min(roughness, 1.5) * AMPF * size
    noise = _loop_noise(len(d), rng, wavelength=2.0 * step)
    return d + normal * (np.clip(noise * 1.6, -1, 1) * amp)[:, None]


def _box(pts: np.ndarray, doc: MapDocument, pad: int) -> tuple[int, int, int, int] | None:
    x0 = max(int(math.floor(pts[:, 0].min())) - pad, 0)
    y0 = max(int(math.floor(pts[:, 1].min())) - pad, 0)
    x1 = min(int(math.ceil(pts[:, 0].max())) + pad, doc.width)
    y1 = min(int(math.ceil(pts[:, 1].max())) + pad, doc.height)
    if x1 <= x0 or y1 <= y0:
        return None
    return x0, y0, x1, y1


def _fill(pts: np.ndarray, box, ss: int = 1) -> np.ndarray:
    """Polygon fill into the box. ss == 1: bool array; ss > 1: float32 coverage in [0, 1]."""
    x0, y0, x1, y1 = box
    cw, ch = x1 - x0, y1 - y0
    img = Image.new("L", (cw * ss, ch * ss), 0)
    ImageDraw.Draw(img).polygon([((x - x0) * ss, (y - y0) * ss) for x, y in pts], fill=255)
    a = np.asarray(img)
    if ss == 1:
        return a > 0
    return (a.reshape(ch, ss, cw, ss).mean(axis=(1, 3)) / 255.0).astype(np.float32)


def _blur(a: np.ndarray, r: int) -> np.ndarray:
    """Separable box blur of radius r with edge replication (NumPy only)."""
    out = a.astype(np.float32)
    for axis in (0, 1):
        pad = [(0, 0), (0, 0)]
        pad[axis] = (r + 1, r)
        p = np.pad(out, pad, mode="edge")
        c = np.cumsum(p, axis=axis, dtype=np.float64)
        n = out.shape[axis]
        hi = np.take(c, np.arange(2 * r + 1, 2 * r + 1 + n), axis=axis)
        lo = np.take(c, np.arange(0, n), axis=axis)
        out = ((hi - lo) / (2 * r + 1)).astype(np.float32)
    return out


def _changed(before: np.ndarray, after: np.ndarray) -> np.ndarray:
    return (before != after).any(axis=-1)


def _sub_seed(rng: np.random.Generator) -> int:
    return int(rng.integers(0, 2**31 - 1))


# ---------------------------------------------------------------------------- land
def paint_land(
    doc: MapDocument,
    polygon: list[Point],
    mode: Literal["add", "subtract"] = "add",
    roughness: float = 0.5,
    seed: int = 0,
) -> int:
    """Paint (or erase) land inside a noise-displaced polygon.

    add: alpha inside becomes max(existing, land_alpha(h)); h is ~0 at the coast and
    rises toward the interior (distance-to-edge via blurred fill, modulated by fbm).
    Existing land is never lowered.
    subtract: land inside becomes water (shallow at the coast, deeper inward; existing
    water is only ever deepened), freshwater R is cleared and the ``ground`` paint is
    cleared too (it would be invisible under water and reappear if land is re-added).
    ``roughness`` scales the edge displacement; 0 gives the plain polygon.
    """
    if mode not in ("add", "subtract"):
        raise ValueError(f"mode must be 'add' or 'subtract', got {mode!r}")
    pts = _clean(polygon, doc, 3)
    if pts is None:
        return 0
    rng = np.random.default_rng(seed)
    pts = _displace(pts, max(float(roughness), 0.0), rng)
    r = int(np.clip(0.2 * min(np.ptp(pts[:, 0]), np.ptp(pts[:, 1])), 2, 150))
    box = _box(pts, doc, r + 2)
    if box is None:
        return 0
    x0, y0, x1, y1 = box
    inside = _fill(pts, box)
    if not inside.any():
        return 0
    b = _blur(_blur(inside.astype(np.float32), r), r)
    h = np.clip((b - 0.5) * 2.0, 0.0, 1.0)
    h = np.clip(h * (0.55 + 0.9 * fbm(h.shape, max(8, r), 4, _sub_seed(rng))), 0.0, 1.0)

    mask = doc.raster("mask")[y0:y1, x0:x1]
    ground = doc.raster("ground")[y0:y1, x0:x1]
    m_before, g_before = mask.copy(), ground.copy()
    alpha = mask[..., 3]
    if mode == "add":
        mask[..., 3] = np.where(inside, np.maximum(alpha, rs.land_alpha(h)), alpha)
    else:
        wa = rs.water_alpha(h)
        new = np.where(alpha >= rs.SEA_LEVEL_ALPHA, wa, np.minimum(alpha, wa))
        mask[..., 3] = np.where(inside, new, alpha)
        mask[inside, rs.FRESHWATER_CHANNEL] = 0
        ground[inside] = 0
    return int((_changed(m_before, mask) | _changed(g_before, ground)).sum())


_KINDS = ("continent", "archipelago", "island")


def generate_landmass(
    doc: MapDocument,
    coverage: float,
    seed: int,
    kind: Literal["continent", "archipelago", "island"],
) -> int:
    """Replace the whole ``mask`` with generated terrain of the given land ``coverage``.

    The coverage fraction is exact up to ties (threshold chosen by quantile). Freshwater
    R is reset; ``ground`` and ``water_tint`` are left untouched.
    """
    if kind not in _KINDS:
        raise ValueError(f"kind must be one of {_KINDS}, got {kind!r}")
    cov = float(np.clip(coverage, 0.0, 1.0))
    h, w = doc.height, doc.width
    rng = np.random.default_rng(seed)
    u = np.linspace(-1, 1, w, dtype=np.float32)[None, :]
    v = np.linspace(-1, 1, h, dtype=np.float32)[:, None]
    warp = fbm((h, w), 0.25 * max(h, w), 5, _sub_seed(rng))
    if kind == "archipelago":
        f = np.full((h, w), -np.inf, dtype=np.float32)
        for _ in range(int(rng.integers(6, 11))):
            cx, cy = rng.uniform(-0.75, 0.75, 2)
            rad = rng.uniform(0.12, 0.3)
            d = np.hypot(u - cx, v - cy) / rad
            f = np.maximum(f, 1.0 - d)
        f = f + 0.5 * (warp - 0.5)
    else:
        r = np.hypot(u, v * 0.9)
        f = (1.0 - r) + (0.7 if kind == "continent" else 0.3) * (warp - 0.5)
    f = f.astype(np.float32)
    fmin, fmax = float(f.min()), float(f.max())
    if cov >= 1.0:
        thr = fmin - 1e-3
    elif cov <= 0.0:
        thr = fmax + 1e-3
    else:
        thr = float(np.quantile(f, 1.0 - cov))
    land = f > thr
    height = np.clip((f - thr) / max(fmax - thr, 1e-6), 0, 1) ** 0.7
    depth = np.clip((thr - f) / max(thr - fmin, 1e-6), 0, 1)
    mask = doc.raster("mask")
    before = mask.copy()
    mask[...] = 0
    mask[..., 3] = np.where(land, rs.land_alpha(height), rs.water_alpha(depth))
    return int(_changed(before, mask).sum())


def raise_area(doc: MapDocument, polygon: list[Point], amount: float) -> int:
    """Add ``amount`` (height01 units, may be negative) to land pixels inside the polygon.

    Water is never touched or turned into land; land stays within [128, 255].
    """
    pts = _clean(polygon, doc, 3)
    if pts is None or not math.isfinite(amount):
        return 0
    box = _box(pts, doc, 0)
    if box is None:
        return 0
    x0, y0, x1, y1 = box
    inside = _fill(pts, box)
    mask = doc.raster("mask")[y0:y1, x0:x1]
    alpha = mask[..., 3]
    sel = inside & rs.is_land(alpha)
    new = np.clip(np.rint(alpha.astype(np.float64) + amount * (255 - rs.SEA_LEVEL_ALPHA)),
                  rs.SEA_LEVEL_ALPHA, 255).astype(np.uint8)
    n = int((sel & (new != alpha)).sum())
    mask[..., 3] = np.where(sel, new, alpha)
    return n


# ------------------------------------------------------------------- freshwater
def _write_freshwater(doc: MapDocument, box, cov: np.ndarray) -> int:
    x0, y0, x1, y1 = box
    full = np.zeros((doc.height, doc.width), dtype=np.float32)
    full[y0:y1, x0:x1] = cov
    return rs.paint_freshwater(doc.raster("mask"), full)


def add_lake(doc: MapDocument, polygon: list[Point], roughness: float = 0.3, seed: int = 0) -> int:
    """Freshwater lake: anti-aliased coverage of the displaced polygon, written to mask R."""
    pts = _clean(polygon, doc, 3)
    if pts is None:
        return 0
    pts = _displace(pts, max(float(roughness), 0.0), np.random.default_rng(seed))
    box = _box(pts, doc, 2)
    if box is None:
        return 0
    return _write_freshwater(doc, box, _fill(pts, box, _SS))


def add_river(doc: MapDocument, points: list[Point], width: float = 3.0, seed: int = 0) -> int:
    """Meandering anti-aliased river through ``points`` (first point is the source).

    The width tapers from ~25% at the source to full width over the first third of the
    length. The meander amplitude is ~4-8% of each segment's length and the offset is
    zero at every input point, so the river always passes
    through them. Written to mask R on land only.
    """
    pts = _clean(points, doc, 2)
    if pts is None or not math.isfinite(width) or width <= 0:
        return 0
    keep = np.ones(len(pts), bool)
    keep[1:] = np.hypot(*np.diff(pts, axis=0).T) > 1e-6
    pts = pts[keep]
    if len(pts) < 2:
        return 0
    rng = np.random.default_rng(seed)
    samples = []
    for p, q in zip(pts[:-1], pts[1:]):
        d = q - p
        length = float(np.hypot(*d))
        n = max(2, int(math.ceil(length / 0.5)))
        u = np.arange(n) / n
        perp = np.array([-d[1], d[0]]) / length
        amp = rng.uniform(0.04, 0.08) * length  # meander scales with segment length
        off = np.zeros(n)
        for k in range(1, 9):
            c = rng.uniform(-1, 1)
            if k == 1:
                c = rng.choice([-1.0, 1.0]) * rng.uniform(0.6, 1.0)
            off += c * amp / k * np.sin(k * np.pi * u)
        samples.append(p + np.outer(u, d) + np.outer(off, perp))
    samples.append(pts[-1:])
    s = np.vstack(samples)
    arc = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(s, axis=0).T))])
    t = np.clip(arc / max(0.35 * arc[-1], 1e-9), 0, 1)
    radius = 0.5 * width * (0.25 + 0.75 * t * t * (3 - 2 * t))

    pad = int(math.ceil(width)) + 2
    box = _box(s, doc, pad)
    if box is None:
        return 0
    x0, y0, x1, y1 = box
    cw, ch = x1 - x0, y1 - y0
    img = Image.new("L", (cw * _SS, ch * _SS), 0)
    draw = ImageDraw.Draw(img)
    for (x, y), rad in zip(s, radius):
        cx, cy, rr = (x - x0) * _SS, (y - y0) * _SS, rad * _SS
        draw.ellipse((cx - rr, cy - rr, cx + rr, cy + rr), fill=255)
    a = np.asarray(img).reshape(ch, _SS, cw, _SS).mean(axis=(1, 3)) / 255.0
    return _write_freshwater(doc, box, a.astype(np.float32))


# ------------------------------------------------------------------------ ground
def paint_ground(doc: MapDocument, polygon: list[Point], color_index: int,
                 softness: float = 0.5) -> int:
    """Paint theme ground colour ``color_index`` over the polygon (alpha-over compositing).

    ``softness`` in [0, 1] feathers the edge (blur radius up to 10 px). Paints regardless
    of land/water, since ground under water is invisible.
    """
    colors = doc.root["theme"]["ground_colors"]
    if not isinstance(color_index, (int, np.integer)) or not 0 <= color_index < len(colors):
        raise IndexError(
            f"color_index {color_index!r} out of range: theme has {len(colors)} ground colours")
    c = colors[color_index]
    rgba = np.array([c.r, c.g, c.b, c.a], dtype=np.float64)
    rgb_n = np.rint(np.clip(rgba[:3], 0, 1) * 255).astype(np.float32)
    ca = float(np.clip(rgba[3], 0, 1))
    pts = _clean(polygon, doc, 3)
    if pts is None:
        return 0
    r = int(round(float(np.clip(softness, 0, 1)) * 10))
    box = _box(pts, doc, 2 * r + 2)  # double box blur spreads ~2r
    if box is None:
        return 0
    x0, y0, x1, y1 = box
    cov = _fill(pts, box, _SS)
    if r > 0:
        cov = _blur(_blur(cov, r), r) if r > 1 else _blur(cov, r)
    an = (cov * ca).astype(np.float32)
    g = doc.raster("ground")[y0:y1, x0:x1]
    before = g.copy()
    ae = g[..., 3].astype(np.float32) / 255.0
    ao = an + ae * (1 - an)
    num = rgb_n * an[..., None] + g[..., :3].astype(np.float32) * (ae * (1 - an))[..., None]
    rgb = num / np.maximum(ao, 1e-6)[..., None]
    sel = an > 0
    g[..., :3] = np.where(sel[..., None], np.rint(np.clip(rgb, 0, 255)), g[..., :3]).astype(np.uint8)
    g[..., 3] = np.where(sel, np.rint(np.clip(ao, 0, 1) * 255), g[..., 3]).astype(np.uint8)
    return int(_changed(before, g).sum())
