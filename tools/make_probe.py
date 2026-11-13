"""Spike B helper: write synthetic probe maps that isolate one raster channel each.

Usage:
    python tools/make_probe.py OUT_DIR [--src tests/fixtures/testmap.wonderdraft_map]

Writes probe_{a,b,c,d,e}.wonderdraft_map into OUT_DIR (never into the source).
All objects (symbols, labels, paths, territories, boxes, windroses) are cleared;
theme and settings are kept from the source map.

  a  mask alpha = left-to-right ramp 0..255, RGB 0
  b  mask alpha 255 everywhere, RGB = 1 in a vertical stripe (x 800..1100)
  c  mask alpha 255 everywhere, ground = red square (alpha 255) at 600..1000 x 300..700
  d  mask alpha 0 on the left half / 255 on the right half,
     water_tint = blue square straddling the boundary (760..1160 x 340..740)
  e  ramp like (a) plus RGB = 1 in a horizontal band (y 480..600) across the ramp
"""
from __future__ import annotations

import argparse
import copy
from pathlib import Path

import numpy as np

from wdmcp.codec import dump_map_bytes, load_map_bytes


def _clear_objects(root: dict) -> None:
    for key in ("symbols", "labels", "paths", "windroses", "boxes"):
        if isinstance(root.get(key), list):
            root[key] = []
    terr = root.get("territories")
    if isinstance(terr, dict) and isinstance(terr.get("territories"), list):
        terr["territories"] = []


def _layer(root: dict, name: str) -> np.ndarray:
    d = root[name].props["data"]
    return np.frombuffer(d["data"], np.uint8).reshape(d["height"], d["width"], 4).copy()


def _store(root: dict, name: str, arr: np.ndarray) -> None:
    root[name].props["data"]["data"] = np.ascontiguousarray(arr, dtype=np.uint8).tobytes()


def build_probes(src: dict) -> dict[str, dict]:
    base = copy.deepcopy(src)
    _clear_objects(base)
    h, w = _layer(base, "mask").shape[:2]
    zero = np.zeros((h, w, 4), np.uint8)
    for name in ("mask", "ground", "water_tint"):
        _store(base, name, zero)

    ramp = np.broadcast_to(np.linspace(0, 255, w).round().astype(np.uint8), (h, w))
    probes: dict[str, dict] = {}

    def mk(tag: str, mask=None, ground=None, tint=None) -> None:
        r = copy.deepcopy(base)
        for name, arr in (("mask", mask), ("ground", ground), ("water_tint", tint)):
            if arr is not None:
                _store(r, name, arr)
        probes[tag] = r

    m = zero.copy(); m[..., 3] = ramp
    mk("a", mask=m)

    m = zero.copy(); m[..., 3] = 255; m[:, 800:1100, :3] = 1
    mk("b", mask=m)

    m = zero.copy(); m[..., 3] = 255
    g = zero.copy(); g[300:700, 600:1000] = (255, 0, 0, 255)
    mk("c", mask=m, ground=g)

    m = zero.copy(); m[:, w // 2:, 3] = 255
    t = zero.copy(); t[340:740, 760:1160] = (0, 0, 255, 255)
    mk("d", mask=m, tint=t)

    m = zero.copy(); m[..., 3] = ramp; m[480:600, :, :3] = 1
    mk("e", mask=m)
    return probes


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("out_dir", type=Path)
    ap.add_argument("--src", type=Path, default=Path("tests/fixtures/testmap.wonderdraft_map"))
    args = ap.parse_args(argv)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    src = load_map_bytes(args.src)
    for tag, root in build_probes(src).items():
        out = args.out_dir / f"probe_{tag}.wonderdraft_map"
        out.write_bytes(dump_map_bytes(root))
        print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
