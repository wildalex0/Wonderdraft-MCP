"""Seeded value noise (NumPy only)."""
from __future__ import annotations

import numpy as np


def _octave(h: int, w: int, cell: float, rng: np.random.Generator) -> np.ndarray:
    gh, gw = int(h / cell) + 3, int(w / cell) + 3
    grid = rng.random((gh, gw), dtype=np.float32)

    def axis(n: int):
        c = np.arange(n, dtype=np.float32) / np.float32(cell)
        i0 = np.floor(c).astype(np.intp)
        f = c - i0
        return i0, f * f * (3.0 - 2.0 * f)  # smoothstep

    y0, fy = axis(h)
    x0, fx = axis(w)
    rows = grid[y0] * (1 - fy)[:, None] + grid[y0 + 1] * fy[:, None]  # (h, gw)
    return rows[:, x0] * (1 - fx) + rows[:, x0 + 1] * fx


def fbm(shape: tuple[int, int], scale: float, octaves: int, seed: int) -> np.ndarray:
    """Fractal value noise, float32 in [0, 1], deterministic per (shape, scale, octaves, seed).

    ``scale`` is the feature size in pixels of the first octave; each further octave
    halves it and halves the amplitude.
    """
    h, w = int(shape[0]), int(shape[1])
    rng = np.random.default_rng(seed)
    total = np.zeros((h, w), dtype=np.float32)
    amp, norm, cell = 1.0, 0.0, max(float(scale), 1.0)
    for _ in range(max(int(octaves), 1)):
        total += np.float32(amp) * _octave(h, w, max(cell, 1.0), rng)
        norm += amp
        amp *= 0.5
        cell /= 2.0
    total /= np.float32(norm)
    # Averaging octaves compresses the range toward 0.5; stretch it back out.
    return np.clip((total - 0.5) * 1.8 + 0.5, 0.0, 1.0).astype(np.float32)
