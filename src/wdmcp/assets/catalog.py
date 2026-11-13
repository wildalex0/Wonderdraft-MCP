"""Catalogue of Wonderdraft assets: built-in pck, built-in packs and user packs."""
from __future__ import annotations

import csv
import difflib
import io
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from PIL import Image

from wdmcp.assets.pck import PckReader
from wdmcp.model.raster_spec import USER_TEXTURE_PREFIX, user_pack_texture

Category = Literal["symbols", "mountains", "trees"]
CATEGORIES: tuple[str, ...] = ("symbols", "mountains", "trees")
DEFAULT_PCK = r"C:\Program Files\Wonderdraft\Wonderdraft.pck"
SYMBOLS_FILE = ".wonderdraft_symbols"
# Built-in style textures of Wonderdraft 1.1.8 (res://textures/<kind>/<name>), for docs and
# tests; validation uses the names indexed from the pck itself. Spellings are the pck's own
# (sic "solid_line_tansparent").
TEXTURE_KINDS: tuple[str, ...] = ("paths", "borders", "frames")
BUILTIN_TEXTURES: dict[str, tuple[str, ...]] = {
    "paths": ("path_blended", "path_circle", "path_dash", "path_dash_dot", "path_dash_dot_dot",
              "path_directional", "path_double_paired", "path_hash_marks", "path_solid",
              "path_solid_outlined"),
    "borders": ("border_dark_dot", "border_dash", "border_gradient", "border_solid"),
    "frames": ("celtic_dotted", "celtic_leaves", "celtic_woven_1", "celtic_woven_2",
               "checkered_opaque", "checkered_transparent", "double_pen_opaque",
               "double_pen_transparent", "double_pen_vignette", "entwined_corner_opaque",
               "entwined_corner_transparent", "hatch_opaque", "hatch_transparent",
               "old_ink_opaque", "old_ink_transparent", "solid_line_opaque",
               "solid_line_tansparent"),
}
_TEXTURE = re.compile(r"^res://textures/(paths|borders|frames)/([^/.][^/]*)\.wonderdraft_image$")
_BUILTIN_SET = re.compile(r"^res://(?:packs/([^/]+)/)?sprites/(symbols|mountains|trees)/([^/]+)/(.+)$")


class AssetNotFound(Exception):
    def __init__(self, key: str, suggestions: list[str] | None = None):
        self.key = key
        self.suggestions = list(suggestions or [])
        msg = f"asset not found: {key!r}"
        if self.suggestions:
            msg += f" (did you mean: {', '.join(self.suggestions)}?)"
        super().__init__(msg)


class PckNotFound(ValueError):
    """Wonderdraft.pck (built-in assets and themes) could not be found."""

    def __init__(self, path: str | None):
        self.path = path
        super().__init__(
            f"Wonderdraft's asset file Wonderdraft.pck was not found at {path}. Install "
            f"Wonderdraft 1.1.8 there, or set the WDMCP_PCK environment variable to the full "
            f"path of Wonderdraft.pck (and restart the server).")


@dataclass(frozen=True)
class VariantMeta:
    """Placement metadata of one sprite: what Wonderdraft writes into a new symbol entry
    (``radius``; ``offset`` = (offset_x, offset_y)) and how it is drawn (``draw_mode``:
    "sample_color" sprites are black-and-white art tinted by the entry's ``sample``)."""
    name: str
    radius: float
    offset: tuple[float, float]
    draw_mode: str


@dataclass
class SymbolSet:
    key: str
    pack: str | None
    category: Category
    name: str
    radius: float
    offset: tuple[float, float]
    draw_mode: str
    variants: list[str] = field(default_factory=list)
    # per-variant metadata keyed by sprite file stem (e.g. "mine_01_flat", "1")
    variant_meta: dict[str, VariantMeta] = field(default_factory=dict)

    def meta(self, texture: str) -> VariantMeta:
        """Metadata for one of this set's sprites, falling back to the set level."""
        stem = texture.rsplit("/", 1)[-1]
        m = self.variant_meta.get(stem)
        if m is None and self.variant_meta:
            low = {k.lower(): v for k, v in self.variant_meta.items()}
            m = low.get(stem.lower())
        return m or VariantMeta(self.name, self.radius, self.offset, self.draw_mode)


def _num(v, default: float) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return default
    return float(v)


def _meta_from(d: dict, base: VariantMeta) -> VariantMeta:
    name = d["name"] if isinstance(d.get("name"), str) and d["name"] else base.name
    mode = d["draw_mode"] if isinstance(d.get("draw_mode"), str) and d["draw_mode"] else base.draw_mode
    return VariantMeta(name, _num(d.get("radius"), base.radius),
                       (_num(d.get("offset_x"), base.offset[0]),
                        _num(d.get("offset_y"), base.offset[1])), mode)


def _parse_symbols(raw: bytes | None, fallback_name: str):
    """Parse a ``.wonderdraft_symbols`` file. Two shapes exist:

    * flat: ``{"name", "radius", "offset_x", "offset_y", "draw_mode"}`` for the whole set;
    * per variant: ``{"<sprite stem>": {...same keys...}, ...}`` (``*_paintable`` sets, many
      packs, user packs such as ``"1": {...}`` for ``1.png``).

    Returns (name, radius, offset, draw_mode, variant_meta). For a per-variant file without
    top-level values, the set-level values are taken from its first variant (sorted by stem)
    so set-wide uses (e.g. scatter spacing) stay sensible."""
    base = VariantMeta(fallback_name, 50.0, (0.0, 0.0), "normal")
    per: dict[str, VariantMeta] = {}
    if raw:
        try:
            data = json.loads(raw.decode("utf-8-sig"))
        except ValueError:  # includes UnicodeDecodeError
            data = None
        if isinstance(data, dict):
            flat = {k: v for k, v in data.items() if not isinstance(v, dict)}
            nested = {k: v for k, v in data.items() if isinstance(v, dict)}
            if flat:
                base = _meta_from(flat, base)
            elif nested:
                first = _meta_from(nested[sorted(nested)[0]], base)
                base = VariantMeta(base.name, first.radius, first.offset, first.draw_mode)
            per = {k: _meta_from(v, base) for k, v in nested.items()}
    return base.name, base.radius, base.offset, base.draw_mode, per


def _env_or(arg, var: str, default: str | None) -> str | None:
    if arg is not None:
        return str(arg)
    return os.environ.get(var) or default


class Catalog:
    def __init__(self, pck: PckReader | None, user_dir: Path | None):
        self._pck = pck
        self._user_dir = user_dir
        self._sets: dict[str, SymbolSet] = {}
        self._themes: dict[str, dict] | None = None
        self._names: dict[str, list[str]] | None = None
        self._textures: dict[str, list[str]] = {k: [] for k in TEXTURE_KINDS}
        if pck is not None:
            self._index_pck()
        if user_dir is not None and user_dir.is_dir():
            self._index_user()

    @classmethod
    def load(cls, pck_path=None, user_dir=None, *, require_pck: bool = True) -> "Catalog":
        """Index the pck (``pck_path``, else $WDMCP_PCK, else the default install path) and the
        user pack folder. A missing pck raises PckNotFound unless ``require_pck`` is False
        (then only user packs are indexed and styles/themes cannot be validated)."""
        pck = _env_or(pck_path, "WDMCP_PCK", DEFAULT_PCK)
        if require_pck and not (pck and Path(pck).is_file()):
            raise PckNotFound(pck)
        appdata = os.environ.get("APPDATA")
        default_user = str(Path(appdata) / "Wonderdraft" / "assets") if appdata else None
        udir = _env_or(user_dir, "WDMCP_USER_DIR", default_user)
        reader = PckReader(pck) if pck and Path(pck).is_file() else None
        return cls(reader, Path(udir) if udir else None)

    # -- indexing (paths only; PNGs are read lazily) ------------------------
    def _index_pck(self) -> None:
        groups: dict[str, list[str]] = {}
        meta: dict[str, tuple[str | None, str, str]] = {}
        symbols: set[str] = set()
        for p in self._pck.paths():
            t = _TEXTURE.match(p)
            if t:
                self._textures[t.group(1)].append(t.group(2))
                continue
            if p.endswith("/" + SYMBOLS_FILE):
                symbols.add(p)
            elif p.endswith(".wonderdraft_image"):
                m = _BUILTIN_SET.match(p)
                if not m or "/" in m.group(4):
                    continue
                d = p.rsplit("/", 1)[0]
                groups.setdefault(d, []).append(p[: -len(".wonderdraft_image")])
                meta[d] = (m.group(1), m.group(2), m.group(3))
        for d, variants in groups.items():
            pack, cat, setname = meta[d]
            sym = d + "/" + SYMBOLS_FILE
            raw = self._pck.read(sym) if sym in symbols else None
            name, radius, offset, mode, per = _parse_symbols(raw, setname)
            self._sets[d] = SymbolSet(d, pack, cat, name, radius, offset, mode, sorted(variants),
                                      per)

    def _index_user(self) -> None:
        for pack_dir in sorted(p for p in self._user_dir.iterdir() if p.is_dir()):
            for cat in CATEGORIES:
                cat_dir = pack_dir / "sprites" / cat
                if not cat_dir.is_dir():
                    continue
                for set_dir in sorted(p for p in cat_dir.iterdir() if p.is_dir()):
                    pngs = sorted(f.name for f in set_dir.iterdir()
                                  if f.is_file() and f.suffix.lower() == ".png")
                    if not pngs:
                        continue
                    base = f"sprites/{cat}/{set_dir.name}"
                    key = f"{USER_TEXTURE_PREFIX}{pack_dir.name}/{base}"  # no splitext: dots are legal in set names
                    sf = set_dir / SYMBOLS_FILE
                    try:
                        raw = sf.read_bytes() if sf.is_file() else None
                    except OSError:
                        raw = None
                    name, radius, offset, mode, per = _parse_symbols(raw, set_dir.name)
                    variants = sorted(user_pack_texture(pack_dir.name, f"{base}/{n}") for n in pngs)
                    self._sets[key] = SymbolSet(key, pack_dir.name, cat, name, radius, offset, mode,
                                                variants, per)

    # -- queries ----------------------------------------------------------
    def sets(self, category: str | None = None, query: str | None = None) -> list[SymbolSet]:
        q = query.lower() if query else None
        out = []
        for s in self._sets.values():
            if category and s.category != category:
                continue
            if q and q not in s.key.lower() and q not in s.name.lower():
                continue
            out.append(s)
        return sorted(out, key=lambda s: s.key)

    def get_set(self, key: str) -> SymbolSet:
        s = self._sets.get(key)
        if s is not None:
            return s
        raise AssetNotFound(key, self._suggest(key))

    def set_of(self, texture: str) -> SymbolSet | None:
        """The set a sprite texture belongs to, or None."""
        return self._sets.get(texture.rsplit("/", 1)[0])

    def variant_meta(self, texture: str) -> VariantMeta | None:
        """Per-sprite metadata (falling back to its set's), or None for an unknown set."""
        s = self.set_of(texture)
        return s.meta(texture) if s is not None else None

    def _suggest(self, key: str) -> list[str]:
        """Close matches against full keys and each set's last path segment; returns full keys."""
        by_last: dict[str, list[str]] = {}
        for k in self._sets:
            by_last.setdefault(k.rsplit("/", 1)[-1], []).append(k)
        query = key.rstrip("/")
        last = query.rsplit("/", 1)[-1]
        found = difflib.get_close_matches(query, list(self._sets), n=5, cutoff=0.6)
        found += [k for m in difflib.get_close_matches(last, list(by_last), n=5, cutoff=0.6)
                  for k in by_last[m]]
        out: list[str] = []
        for k in found:
            if k not in out:
                out.append(k)
        return out[:5]

    # -- style textures (paths, borders, frames) ------------------------------
    def texture_names(self, kind: str) -> list[str] | None:
        """Built-in texture names of ``kind`` (paths|borders|frames) from the pck, or None when
        no pck is loaded (then names cannot be validated)."""
        if kind not in TEXTURE_KINDS:
            raise ValueError(f"unknown texture kind {kind!r}")
        if self._pck is None:
            return None
        return sorted(self._textures[kind])

    def resolve_texture(self, kind: str, name: str) -> str:
        """Validate a style name and return what the map stores: ``res://textures/<kind>/<name>``
        for paths and borders, the bare name for frames. Accepts the bare name or the full res
        path, and ``user://assets/<pack>/textures/<kind>/<name>`` for an existing user-pack
        PNG. Raises AssetNotFound with close matches. Without a pck nothing is validated."""
        name = str(name).strip()
        prefix = f"res://textures/{kind}/"
        names = self.texture_names(kind)
        if name.startswith(USER_TEXTURE_PREFIX) and kind != "frames":
            if names is None or self._user_texture_exists(name, kind):
                return name
            raise AssetNotFound(name, [])
        bare = name[len(prefix):] if name.startswith(prefix) else name
        out = bare if kind == "frames" else prefix + bare
        if names is None or bare in names:
            return out
        close = difflib.get_close_matches(bare, names, n=5, cutoff=0.4)
        raise AssetNotFound(name, close or names[:5])

    def _user_texture_exists(self, texture: str, kind: str) -> bool:
        if self._user_dir is None:
            return False
        rel = texture[len(USER_TEXTURE_PREFIX):]
        parts = rel.split("/")
        if len(parts) != 4 or parts[1:3] != ["textures", kind]:
            return False
        root = self._user_dir.resolve()
        f = (root / (rel + ".png")).resolve()
        return f.is_relative_to(root) and f.is_file()

    def image(self, texture: str) -> Image.Image:
        data = None
        if texture.startswith("res://") and self._pck is not None:
            p = texture + ".wonderdraft_image"
            if p in self._pck:
                data = self._pck.read(p)
        elif texture.startswith(USER_TEXTURE_PREFIX) and self._user_dir is not None:
            rel = texture[len(USER_TEXTURE_PREFIX):]
            root = self._user_dir.resolve()
            f = (root / (rel + ".png")).resolve()
            if f.is_relative_to(root) and f.is_file():
                data = f.read_bytes()
        if data is None:
            raise AssetNotFound(texture, [])
        img = Image.open(io.BytesIO(data))
        img.load()
        return img

    def themes(self) -> dict[str, dict]:
        if self._themes is None:
            out: dict[str, dict] = {}
            if self._pck is not None:
                for p in self._pck.paths():
                    if p.startswith("res://themes/") and p.endswith(".wonderdraft_theme"):
                        name = p[len("res://themes/"):-len(".wonderdraft_theme")]
                        try:
                            out[name] = json.loads(self._pck.read(p).decode("utf-8-sig"))
                        except ValueError:
                            continue
            self._themes = out
        return self._themes

    def get_theme(self, name: str) -> dict:
        t = self.themes()
        if name in t:
            return t[name]
        low = {k.lower(): k for k in t}
        if name.lower() in low:
            return t[low[name.lower()]]
        raise AssetNotFound(name, difflib.get_close_matches(name, list(t), n=5, cutoff=0.5))

    def name_lists(self) -> dict[str, list[str]]:
        """Built-in name pattern lists (first CSV column, e.g. ``%sbrook``)."""
        if self._names is None:
            out: dict[str, list[str]] = {}
            if self._pck is not None:
                for p in self._pck.paths():
                    if p.startswith("res://names/") and p.endswith(".csv"):
                        text = self._pck.read(p).decode("utf-8-sig", "replace")
                        rows = [r[0].strip() for r in csv.reader(io.StringIO(text)) if r and r[0].strip()]
                        out[p[len("res://names/"):-4]] = rows
            self._names = out
        return self._names
