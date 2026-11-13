"""Raster semantics for Wonderdraft 1.1.8 maps (see docs/format.md, "Spike B").

``mask`` alpha is a height field: water 0-127, land 128-255 (water_level 0.5).
``ground`` / ``water_tint`` are direct RGBA paint. A fresh map is all (0,0,0,0).
"""
from __future__ import annotations

import os

import numpy as np

WATER_FILL: tuple[int, int, int, int] = (0, 0, 0, 0)
SEA_LEVEL_ALPHA: int = 128  # minimum land alpha
PAINT_LAND_ALPHA = 153
ERASE_ALPHA = 102
RAISED_ALPHA = 255

GROUND_ENCODING = (
    "Direct RGBA colour paint over land; alpha = opacity; (0,0,0,0) = no paint."
)
WATER_TINT_ENCODING = (
    "Direct RGBA tint over water only; (0,0,0,0) = none."
)
RIVER_ENCODING: str = "PROVISIONAL: unknown until probe_gui is analysed"


def land_alpha(height01: np.ndarray) -> np.ndarray:
    """Height in [0,1] (clipped) -> uint8 alpha in [SEA_LEVEL_ALPHA, 255]."""
    h = np.clip(np.asarray(height01, dtype=np.float64), 0.0, 1.0)
    return np.rint(SEA_LEVEL_ALPHA + h * (255 - SEA_LEVEL_ALPHA)).astype(np.uint8)


def water_alpha(depth01: np.ndarray) -> np.ndarray:
    """Depth 0 (shore) -> 127, 1 (deep) -> 0, as uint8."""
    d = np.clip(np.asarray(depth01, dtype=np.float64), 0.0, 1.0)
    return np.rint((SEA_LEVEL_ALPHA - 1) * (1.0 - d)).astype(np.uint8)


def is_land(alpha: np.ndarray) -> np.ndarray:
    return np.asarray(alpha) >= SEA_LEVEL_ALPHA


def user_pack_texture(pack: str, rel: str) -> str:
    """PROVISIONAL (unverified against a real user-pack map).

    ``rel`` is relative to the pack root, e.g. ``sprites/symbols/<set>/<name>.png``.
    """
    rel = rel.replace("\\", "/")
    root, _ext = os.path.splitext(rel)
    return f"res://packs/{pack}/{root}"
