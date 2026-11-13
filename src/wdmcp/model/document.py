"""MapDocument: a decoded Wonderdraft map with safe save and typed access.

Ids are ``"<kind>:<index>"`` with kind in symbol|label|path|territory. The index
is the entry's position in its list, so removing entries shifts the ids of later
ones; ``remove`` accepts a batch that refers to pre-removal indices.
"""
from __future__ import annotations

import copy
import datetime as _dt
import os
import shutil
import tempfile
from pathlib import Path
from typing import Literal

import numpy as np

from wdmcp.codec import dump_map_bytes, load_map_bytes

from .points import parse_points

_REPO = Path(__file__).resolve().parents[3]
# Task 5 switches this to src/wdmcp/model/blank.wonderdraft_map in one line.
TEMPLATE_PATH = _REPO / "tests" / "fixtures" / "testmap.wonderdraft_map"

# RGBA used to fill new rasters ("all water"); alpha 0 until Spike B settles it.
ALL_WATER_FILL = (0, 0, 0, 0)

RASTERS = ("mask", "ground", "water_tint")
Kind = Literal["symbol", "label", "path", "territory"]
_KINDS = ("symbol", "label", "path", "territory")

_template_cache: dict | None = None


def _template() -> dict:
    """Decoded template (cached; decoding is slow). Always returns a deep copy."""
    global _template_cache
    if _template_cache is None:
        if not TEMPLATE_PATH.exists():
            raise FileNotFoundError(f"New-map template not found: {TEMPLATE_PATH}")
        _template_cache = load_map_bytes(TEMPLATE_PATH)
    return copy.deepcopy(_template_cache)


class MapDocument:
    def __init__(self, root: dict, path: Path | None = None):
        self.root = root
        self.path = Path(path) if path is not None else None
        self._rasters: dict[str, np.ndarray] = {}

    # construction -----------------------------------------------------
    @classmethod
    def open(cls, path) -> "MapDocument":
        return cls(load_map_bytes(path), Path(path))

    @classmethod
    def new(cls, width: int, height: int) -> "MapDocument":
        """Blank all-water map of the given size, keeping the template's theme."""
        root = _template()
        root["symbols"] = []
        root["labels"] = []
        root["paths"] = []
        root["territories"]["territories"] = []
        root["windroses"] = []
        root["boxes"] = []
        root["map_width"] = float(width)
        root["map_height"] = float(height)
        fill = np.array(ALL_WATER_FILL, dtype=np.uint8)
        for name in RASTERS:
            d = root[name].props["data"]
            d["width"], d["height"] = int(width), int(height)
            d["data"] = np.broadcast_to(fill, (height, width, 4)).tobytes()
        return cls(root)

    # basics -----------------------------------------------------------
    @property
    def width(self) -> int:
        return int(self.root["map_width"])

    @property
    def height(self) -> int:
        return int(self.root["map_height"])

    def raster(self, name: Literal["mask", "ground", "water_tint"]) -> np.ndarray:
        """Writable (h, w, 4) uint8 array, written back into the map on save."""
        if name not in RASTERS:
            raise ValueError(f"unknown raster {name!r}; expected one of {RASTERS}")
        if name not in self._rasters:
            d = self.root[name].props["data"]
            arr = np.frombuffer(d["data"], dtype=np.uint8).copy()
            self._rasters[name] = arr.reshape(d["height"], d["width"], 4)
        return self._rasters[name]

    # entries ----------------------------------------------------------
    def _list(self, kind: str) -> list:
        if kind == "symbol":
            return self.root["symbols"]
        if kind == "label":
            return self.root["labels"]
        if kind == "path":
            return self.root["paths"]
        if kind == "territory":
            return self.root["territories"]["territories"]
        raise ValueError(f"unknown kind {kind!r}; expected one of {_KINDS}")

    def _split(self, id: str) -> tuple[str, int]:
        kind, sep, idx = str(id).partition(":")
        if not sep or not idx.isdigit():
            raise ValueError(f"malformed id {id!r}; expected '<kind>:<index>'")
        self._list(kind)  # validates kind
        return kind, int(idx)

    def add(self, kind: Kind, entry: dict) -> str:
        lst = self._list(kind)
        lst.append(entry)
        return f"{kind}:{len(lst) - 1}"

    def get(self, id: str) -> dict:
        kind, i = self._split(id)
        lst = self._list(kind)
        if i >= len(lst):
            raise KeyError(f"no such entry: {id!r} ({len(lst)} {kind} entries)")
        return lst[i]

    def update(self, id: str, **fields) -> dict:
        entry = self.get(id)
        entry.update(fields)
        return entry

    def remove(self, ids: list[str]) -> int:
        """Remove entries; all ids refer to the pre-removal indices."""
        parsed = {self._split(i) for i in ids}
        for kind, i in parsed:
            if i >= len(self._list(kind)):
                raise KeyError(f"no such entry: {kind}:{i}")
        for kind, i in sorted(parsed, key=lambda p: p[1], reverse=True):
            del self._list(kind)[i]
        return len(parsed)

    def list(self, kind: Kind, bbox: tuple[float, float, float, float] | None = None
             ) -> list[tuple[str, dict]]:
        """Entries of a kind; bbox=(x0, y0, x1, y1) in map pixels filters them
        (by position for symbols/labels, by any point inside for paths/territories)."""
        out = []
        for i, e in enumerate(self._list(kind)):
            if bbox is None or self._in_bbox(kind, e, bbox):
                out.append((f"{kind}:{i}", e))
        return out

    @staticmethod
    def _in_bbox(kind: str, e: dict, bbox) -> bool:
        x0, y0, x1, y1 = bbox
        if kind in ("symbol", "label"):
            p = e["position"]
            pts = [(p.x, p.y)]
        else:
            pts = parse_points(e["points"])
        return any(x0 <= x <= x1 and y0 <= y <= y1 for x, y in pts)

    # saving -----------------------------------------------------------
    def save(self, path: Path | None = None) -> Path:
        target = Path(path) if path is not None else self.path
        if target is None:
            raise ValueError("no path to save to; pass one to save()")
        for name, arr in self._rasters.items():
            self.root[name].props["data"]["data"] = arr.tobytes()
        data = dump_map_bytes(self.root)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            bak = target.with_name(f"{target.stem}.{stamp}.bak")
            n = 1
            while bak.exists():
                bak = target.with_name(f"{target.stem}.{stamp}-{n}.bak")
                n += 1
            shutil.copy2(target, bak)
        fd, tmp = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, target)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        self.path = target
        return target
