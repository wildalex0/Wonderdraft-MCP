"""Server session: the open map, a lazy asset catalog, bounded undo and the preview dir."""
from __future__ import annotations

import copy
import functools
import logging
import tempfile
import zlib
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from mcp.server.mcpserver.exceptions import ToolError

from wdmcp.assets.catalog import AssetNotFound, Catalog
from wdmcp.codec.types import Color, Rect2, Transform2D, Vector2
from wdmcp.model.document import RASTERS, MapDocument

log = logging.getLogger("wdmcp")

NO_MAP = "No map is open. Call new_map or open_map first."
UNDO_DEPTH = 20
UNDO_BUDGET = 300 * 1024 * 1024  # bytes of compressed raster snapshots

_ATOMIC = (str, int, float, bool, bytes, type(None), Vector2, Color, Rect2, Transform2D)


def _copy(v):
    """Deep copy that shares immutable values (frozen codec types, bytes)."""
    if isinstance(v, _ATOMIC):
        return v
    if type(v) is dict:
        return {k: _copy(x) for k, x in v.items()}
    if type(v) is list:
        return [_copy(x) for x in v]
    return copy.deepcopy(v)


@dataclass
class Snapshot:
    label: str
    root: dict
    rasters: dict[str, tuple[tuple[int, ...], bytes]] = field(default_factory=dict)
    # set when the step replaced the whole map: (previous doc, its history, its dirty flag)
    prev: tuple | None = None

    @property
    def size(self) -> int:
        return sum(len(b) for _, b in self.rasters.values())


class Session:
    def __init__(self, catalog: Catalog | None = None):
        self.current: MapDocument | None = None
        self._catalog = catalog
        self.history: list[Snapshot] = []
        self.history_budget = UNDO_BUDGET
        self.dirty = False
        self._preview_dir: Path | None = None
        self._preview_n = 0

    # catalog ----------------------------------------------------------
    @property
    def catalog(self) -> Catalog:
        if self._catalog is None:
            log.info("loading asset catalog")
            self._catalog = Catalog.load()
        return self._catalog

    # documents --------------------------------------------------------
    def doc(self) -> MapDocument:
        if self.current is None:
            raise ToolError(NO_MAP)
        return self.current

    def set_current(self, doc: MapDocument, dirty: bool) -> None:
        self.current = doc
        self.history.clear()
        self.dirty = dirty

    def replace_current(self, doc: MapDocument, dirty: bool, label: str) -> bool:
        """Make ``doc`` current like set_current, but keep the previous map (and its undo
        history) as one undo step. Returns True if an unsaved map was replaced."""
        old, hist, was_dirty = self.current, self.history, self.dirty
        self.current, self.history, self.dirty = doc, [], dirty
        if old is not None:
            self.history.append(Snapshot(label, {}, prev=(old, hist, was_dirty)))
        return old is not None and was_dirty

    # preview files ----------------------------------------------------
    def preview_path(self, doc: MapDocument) -> Path:
        """``<stem>.preview.png`` next to a saved map, else a file in the session temp dir."""
        if doc.path is not None:
            return doc.path.with_name(f"{doc.path.stem}.preview.png")
        return self.temp_preview_path()

    def temp_preview_path(self) -> Path:
        if self._preview_dir is None:
            self._preview_dir = Path(tempfile.mkdtemp(prefix="wdmcp-"))
        self._preview_n += 1
        return self._preview_dir / f"preview-{self._preview_n:03d}.png"

    # undo -------------------------------------------------------------
    def _snapshot(self, doc: MapDocument, label: str, rasters) -> Snapshot:
        keep = {n: doc.root[n] for n in RASTERS if n in doc.root}
        root = {k: (keep[k] if k in keep else _copy(v)) for k, v in doc.root.items()}
        snap = Snapshot(label, root)
        for name in rasters:
            arr = doc.raster(name)
            snap.rasters[name] = (arr.shape, zlib.compress(arr.tobytes(), 1))
        return snap

    def _restore(self, doc: MapDocument, snap: Snapshot) -> None:
        doc.root = snap.root
        for name, (shape, blob) in snap.rasters.items():
            doc.raster(name)[...] = np.frombuffer(zlib.decompress(blob), dtype=np.uint8).reshape(shape)

    @contextmanager
    def mutation(self, label: str, rasters=()):
        """Snapshot before a mutation; roll back if it raises, else keep it for undo()."""
        doc = self.doc()
        snap = self._snapshot(doc, label, rasters)
        try:
            yield doc
        except BaseException:
            self._restore(doc, snap)
            raise
        self.history.append(snap)
        while len(self.history) > UNDO_DEPTH or (
                len(self.history) > 1 and sum(s.size for s in self.history) > self.history_budget):
            self.history.pop(0)
        self.dirty = True

    def undo(self) -> str:
        doc = self.doc()
        if not self.history:
            raise ToolError("Nothing to undo.")
        snap = self.history.pop()
        if snap.prev is not None:
            self.current, self.history, self.dirty = snap.prev
            return snap.label
        self._restore(doc, snap)
        self.dirty = True
        return snap.label


def tool_errors(fn):
    """Turn expected failures into ToolError so the client sees the message."""
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        try:
            return fn(*a, **kw)
        except ToolError:
            raise
        except KeyError as e:
            raise ToolError(str(e.args[0]) if e.args else "not found") from e
        except (AssetNotFound, ValueError, IndexError, TypeError, OSError) as e:
            raise ToolError(str(e)) from e
    return wrapper
