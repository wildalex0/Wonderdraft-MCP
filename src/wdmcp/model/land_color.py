"""Land colour under a point: the ``sample`` colour for "sample_color" symbols.

Wonderdraft tints black-and-white ("paintable") sprites with the entry's ``sample`` colour,
which it samples from the canvas under the symbol when placing it. This module computes a
close stand-in from the map data, using the same mapping the preview renderer uses for land:
the theme ``ground_colors`` interpolated by land height (mask alpha 128..255 -> 0..1),
alpha-blended with any ``ground`` raster paint at that pixel, alpha forced to 1. It cannot
include Wonderdraft's paper texture, so it is a little darker than Wonderdraft's own samples.
"""
from __future__ import annotations

import numpy as np

from wdmcp.codec.types import Color
from wdmcp.model import raster_spec as rs

NEAREST_LAND_PX = 64
_DEFAULT_GROUND = (0.90, 0.82, 0.67)


def ground_rgb(height01: np.ndarray, palette) -> np.ndarray:
    """Interpolate ``palette`` (n x 3, any units) by ``height01`` in [0, 1] -> (..., 3)."""
    pal = np.asarray(palette, np.float32)
    height01 = np.asarray(height01, np.float32)
    if len(pal) == 1:
        return np.broadcast_to(pal[0], height01.shape + (3,)).copy()
    t = np.clip(height01, 0, 1) * (len(pal) - 1)
    i = np.minimum(t.astype(int), len(pal) - 2)
    f = (t - i)[..., None]
    return pal[i] * (1 - f) + pal[i + 1] * f


def theme_ground_palette(doc) -> np.ndarray:
    """Theme ground colours as an (n, 3) float array in 0-1."""
    cols = []
    for c in (doc.root.get("theme") or {}).get("ground_colors") or []:
        if isinstance(c, Color):
            cols.append((c.r, c.g, c.b))
    return np.asarray(cols or [_DEFAULT_GROUND], np.float32)


def _nearest_land(alpha: np.ndarray, x: int, y: int, radius: int) -> tuple[int, int] | None:
    h, w = alpha.shape
    x0, y0, x1, y1 = max(x - radius, 0), max(y - radius, 0), min(x + radius + 1, w), min(y + radius + 1, h)
    ys, xs = np.nonzero(rs.is_land(alpha[y0:y1, x0:x1]))
    if len(xs) == 0:
        return None
    d = (xs + x0 - x) ** 2 + (ys + y0 - y) ** 2
    i = int(np.argmin(d))
    return int(xs[i] + x0), int(ys[i] + y0)


def land_color_at(doc, x: float, y: float) -> Color:
    """Land colour at map pixel (x, y) as an opaque Color. On water the nearest land pixel
    within NEAREST_LAND_PX is used; failing that, the first theme ground colour."""
    pal = theme_ground_palette(doc)
    mask = doc.raster("mask")
    h, w = mask.shape[:2]
    xi = min(max(int(x), 0), w - 1)
    yi = min(max(int(y), 0), h - 1)
    alpha = mask[..., 3]
    if not rs.is_land(alpha[yi, xi]):
        spot = _nearest_land(alpha, xi, yi, NEAREST_LAND_PX)
        if spot is None:
            r, g, b = (float(v) for v in pal[0])
            return Color(r, g, b, 1.0)
        xi, yi = spot
    h01 = (float(alpha[yi, xi]) - rs.SEA_LEVEL_ALPHA) / (255.0 - rs.SEA_LEVEL_ALPHA)
    rgb = ground_rgb(np.float32(h01), pal).astype(np.float64)
    paint = doc.raster("ground")[yi, xi].astype(np.float64)
    a = paint[3] / 255.0
    if a > 0:
        rgb = rgb * (1 - a) + paint[:3] / 255.0 * a
    r, g, b = (float(min(max(v, 0.0), 1.0)) for v in rgb)
    return Color(r, g, b, 1.0)
