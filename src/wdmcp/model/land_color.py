"""Land colours: the preview's height-graded ground mapping, and the ``sample`` colour for
"sample_color" symbols.

Wonderdraft tints black-and-white ("paintable") sprites with the entry's ``sample`` colour.
Its own samples are NOT graded by height: every paintable symbol in the testmap fixture
stores the same colour (0.946, 0.851, 0.700), even over water, close to the theme's first
ground colour (0.902, 0.818, 0.673); the small difference is the paper texture, which we do
not model. So :func:`land_color_at` returns ``ground_colors[0]`` alpha-blended with any
``ground`` raster paint at the pixel, alpha forced to 1. The preview keeps its own
height-graded land shading via :func:`ground_rgb`.
"""
from __future__ import annotations

import numpy as np

from wdmcp.codec.types import Color

_DEFAULT_GROUND = (0.90, 0.82, 0.67)


def ground_rgb(height01: np.ndarray, palette) -> np.ndarray:
    """Preview land shading: interpolate ``palette`` (n x 3, any units) by ``height01`` in
    [0, 1] -> (..., 3). Not used for symbol samples."""
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


def land_color_at(doc, x: float, y: float) -> Color:
    """Symbol ``sample`` colour at map pixel (x, y), like Wonderdraft's: the theme's base
    ground colour (``ground_colors[0]``) blended with any ground paint at that pixel, opaque.
    Height and land/water do not matter."""
    rgb = theme_ground_palette(doc)[0].astype(np.float64)
    g = doc.raster("ground")
    h, w = g.shape[:2]
    xi = min(max(int(x), 0), w - 1)
    yi = min(max(int(y), 0), h - 1)
    paint = g[yi, xi].astype(np.float64)
    a = paint[3] / 255.0
    if a > 0:
        rgb = rgb * (1 - a) + paint[:3] / 255.0 * a
    r, gg, b = (float(min(max(v, 0.0), 1.0)) for v in rgb)
    return Color(r, gg, b, 1.0)
