"""Placement ops: symbols, scatter, mountain ranges, forests, settlements, labels,
paths and territories.

Conventions
-----------
* Coordinates are map pixels, origin top-left. Determinism: all randomness comes
  from ``numpy.random.default_rng(seed)``.
* Single items (``add_symbol``, ``add_label``, ``add_settlement``) raise
  ``ValueError`` for an out-of-map position. Batch ops (``scatter``,
  ``mountain_range``, ``forest``) drop out-of-map (and, with ``land_only``,
  non-land) samples and return the dropped count. Paths/territories clamp their
  points to the map rectangle and still succeed (see ``clamp_points``).
* Draw order: ``root["symbols"]`` is kept sorted by ``(z_index, position.y)`` by
  inserting each new symbol after the last entry that sorts <= it, so lower
  symbols draw in front. Ids are positional: inserting shifts the ids of later
  existing symbols; the ids returned are those of the new entries AFTER insertion.
* Symbol ``scale`` is a multiplier on ``BASE_SCALE`` (1.0 for built-in ``res://``
  assets, as in Wonderdraft-saved maps) or ``USER_BASE_SCALE`` (0.25, what
  Wonderdraft places user-pack symbols at).
* ``density`` (scatter / forest / mountain_range) is the target number of symbols
  per 100x100 px of area; min spacing may reduce the actual count.
"""
from __future__ import annotations

import bisect
from typing import Literal

import numpy as np

from wdmcp.assets.catalog import Catalog, SymbolSet
from wdmcp.codec.types import Color
from wdmcp.model import raster_spec as rs
from wdmcp.model import template
from wdmcp.model.document import MapDocument
from wdmcp.ops import scatter as _sc

Point = tuple[float, float]
BASE_SCALE = 1.0
USER_BASE_SCALE = 0.25
LABEL_GAP = 6.0
_TYPE = {"mountains": "mountain", "trees": "tree", "symbols": "symbol"}

# kind -> keywords searched (in order) among built-in (res://) "symbols" sets.
SETTLEMENT_KEYWORDS: dict[str, list[str]] = {
    "city": ["city", "town"],
    "town": ["town"],
    "village": ["village"],
    "castle": ["castle"],
    "fort": ["fort", "castle"],
    "ruin": ["ruin"],
    "port": ["port", "harbor", "harbour", "dock", "ship"],
    "temple": ["temple", "church", "shrine", "cathedral"],
    "mine": ["mine"],
}

# preset -> fallback chain ("*" = first available)
PRESET_FALLBACKS: dict[str, list[str]] = {
    "Region": ["Region", "Terrain", "Town", "*"],
    "City": ["City", "Town", "*"],
    "Water": ["Water", "Town", "*"],
}


# ------------------------------------------------------------------ helpers
def _color(c) -> Color | None:
    if c is None or isinstance(c, Color):
        return c
    t = tuple(float(v) for v in c)
    if len(t) == 3:
        t += (1.0,)
    if len(t) != 4:
        raise ValueError(f"colour must be a Color or an (r, g, b[, a]) tuple, got {c!r}")
    return Color(*t)


def _in_map(doc: MapDocument, x: float, y: float) -> bool:
    return 0 <= x < doc.width and 0 <= y < doc.height


def _require_in_map(doc: MapDocument, pos, what: str) -> tuple[float, float]:
    x, y = float(pos[0]), float(pos[1])
    if not (np.isfinite(x) and np.isfinite(y)) or not _in_map(doc, x, y):
        raise ValueError(f"{what} position ({x}, {y}) is outside the map "
                         f"({doc.width}x{doc.height})")
    return x, y


def clamp_points(doc: MapDocument, points) -> tuple[list[Point], int]:
    """Clamp points to the map rectangle; returns (points, number moved).
    Consecutive duplicates (e.g. created by clamping) are collapsed."""
    out: list[Point] = []
    moved = 0
    for p in points:
        x, y = float(p[0]), float(p[1])
        if not (np.isfinite(x) and np.isfinite(y)):
            raise ValueError(f"non-finite point {p!r}")
        cx = min(max(x, 0.0), float(doc.width))
        cy = min(max(y, 0.0), float(doc.height))
        if (cx, cy) != (x, y):
            moved += 1
        if out and out[-1] == (cx, cy):
            continue
        out.append((cx, cy))
    return out, moved


def _base_scale(texture: str) -> float:
    return USER_BASE_SCALE if texture.startswith("user://") else BASE_SCALE


def _pick_variant(s: SymbolSet, variant: int | None, rng: np.random.Generator) -> str:
    if not s.variants:
        raise ValueError(f"symbol set {s.key!r} has no variants")
    if variant is None:
        return s.variants[int(rng.integers(0, len(s.variants)))]
    if not 0 <= variant < len(s.variants):
        raise ValueError(f"variant {variant} out of range for {s.key!r} "
                         f"(0..{len(s.variants) - 1})")
    return s.variants[variant]


def _make_symbol(s: SymbolSet, texture: str, pos, scale: float, rotation: float,
                 mirror: bool) -> dict:
    k = _base_scale(texture) * float(scale)
    return template.symbol_entry(
        texture, pos, rotation=rotation, scale=(k, k),
        offset=s.offset, radius=s.radius, type=_TYPE.get(s.category, "symbol"),
        mirror=mirror)


def _insert_symbols(doc: MapDocument, entries: list[dict]) -> list[str]:
    """Insert keeping ``(z_index, y)`` order; return ids of ``entries`` after insertion."""
    lst = doc.root["symbols"]
    keys = [(e["z_index"], e["position"].y) for e in lst]
    for e in entries:
        k = (e["z_index"], e["position"].y)
        i = bisect.bisect_right(keys, k)
        keys.insert(i, k)
        lst.insert(i, e)
    index = {id(e): i for i, e in enumerate(lst)}
    return [f"symbol:{index[id(e)]}" for e in entries]


# ------------------------------------------------------------------ symbols
def add_symbol(doc: MapDocument, cat: Catalog, set_key: str, pos, variant: int | None = None,
               scale: float = 1.0, rotation: float = 0.0, mirror: bool = False,
               seed: int = 0) -> str:
    s = cat.get_set(set_key)
    x, y = _require_in_map(doc, pos, "symbol")
    rng = np.random.default_rng(seed)
    tex = _pick_variant(s, variant, rng)
    return _insert_symbols(doc, [_make_symbol(s, tex, (x, y), scale, rotation, mirror)])[0]


def _place_points(doc: MapDocument, s: SymbolSet, pts: np.ndarray, rng, land_only: bool,
                  scale: float) -> tuple[list[str], int]:
    mask = doc.raster("mask")[..., 3] if land_only else None
    entries: list[dict] = []
    dropped = 0
    for x, y in pts:
        if not _in_map(doc, x, y):
            dropped += 1
            continue
        if land_only and not rs.is_land(mask[int(y), int(x)]):
            dropped += 1
            continue
        tex = _pick_variant(s, None, rng)
        entries.append(_make_symbol(s, tex, (float(x), float(y)), scale,
                                    0.0, bool(rng.integers(0, 2))))
    return _insert_symbols(doc, entries), dropped


def _poly(points, what: str, min_pts: int = 3) -> np.ndarray:
    a = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    a = a[np.isfinite(a).all(axis=1)]
    if len(a) < min_pts:
        raise ValueError(f"{what} needs at least {min_pts} points")
    return a


def scatter(doc: MapDocument, cat: Catalog, set_key: str, polygon, density: float,
            min_spacing: float | None = None, land_only: bool = True, seed: int = 0,
            scale: float = 1.0) -> tuple[list[str], int]:
    """Poisson-disk scatter inside ``polygon``; returns (ids, dropped). Samples
    outside the map, or on water when ``land_only``, are dropped."""
    if density < 0:
        raise ValueError("density must be >= 0")
    s = cat.get_set(set_key)
    poly = _poly(polygon, "polygon")
    rng = np.random.default_rng(seed)
    spacing = max(float(min_spacing) if min_spacing is not None else s.radius * 0.9, 1.0)
    pts = _sc.sample_polygon(rng, poly, density, spacing)
    return _place_points(doc, s, pts, rng, land_only, scale)


def mountain_range(doc: MapDocument, cat: Catalog, set_key: str, polyline, width: float,
                   density: float, seed: int = 0) -> tuple[list[str], int]:
    """Mountains in a ribbon of ``width`` px along ``polyline`` (land only)."""
    if density < 0:
        raise ValueError("density must be >= 0")
    s = cat.get_set(set_key)
    line = _poly(polyline, "polyline", 2)
    rng = np.random.default_rng(seed)
    pts = _sc.sample_ribbon(rng, line, float(width), density, max(s.radius * 0.9, 1.0))
    return _place_points(doc, s, pts, rng, True, 1.0)


def forest(doc: MapDocument, cat: Catalog, set_key: str, polygon, density: float,
           seed: int = 0) -> tuple[list[str], int]:
    """Trees inside ``polygon`` (land only)."""
    return scatter(doc, cat, set_key, polygon, density, land_only=True, seed=seed)


# ------------------------------------------------------------------ labels
def label_preset_used(doc: MapDocument, preset: str) -> str:
    """Name of the theme preset that ``preset`` resolves to (with fallbacks)."""
    presets = doc.root["theme"]["label_presets"]
    names = list(presets)
    if not names:
        raise ValueError("the map's theme has no label presets")
    low = {n.lower(): n for n in names}
    for cand in PRESET_FALLBACKS.get(preset, [preset]):
        if cand == "*":
            return names[0]
        if cand in presets:
            return cand
        if cand.lower() in low:
            return low[cand.lower()]
    raise ValueError(f"unknown label preset {preset!r}; available: {', '.join(names)}")


def _label_kwargs(doc: MapDocument, preset: str) -> dict:
    p = doc.root["theme"]["label_presets"][label_preset_used(doc, preset)]
    return dict(font=p["font_name"], size=int(round(p["font_size"])),
                color=p["font_color"],
                outline_size=int(round(p["font_outline_width"])),
                outline_color=p["font_outline_color"])


def add_label(doc: MapDocument, text: str, pos, preset: str = "Town", size: int | None = None,
              rotation: float = 0.0, curve: float = 0.0, color=None) -> str:
    x, y = _require_in_map(doc, pos, "label")
    kw = _label_kwargs(doc, preset)
    if size is not None:
        kw["size"] = int(size)
    c = _color(color)
    if c is not None:
        kw["color"] = c
    return doc.add("label", template.label_entry(text, (x, y), rotation=rotation,
                                                 curve=curve, **kw))


# ------------------------------------------------------------------ settlements
def default_settlement_set(cat: Catalog, kind: str) -> str:
    """Pick a built-in symbols set for ``kind`` via SETTLEMENT_KEYWORDS."""
    if kind not in SETTLEMENT_KEYWORDS:
        raise ValueError(f"unknown settlement kind {kind!r}; expected one of "
                         f"{', '.join(SETTLEMENT_KEYWORDS)}")
    builtin = [s for s in cat.sets("symbols") if s.key.startswith("res://") and s.variants]
    for kw in SETTLEMENT_KEYWORDS[kind]:
        for s in builtin:
            if kw in s.key.lower() or kw in s.name.lower():
                return s.key
    core = [s for s in builtin if s.key.startswith("res://sprites/")]
    if core or builtin:
        return (core or builtin)[0].key
    anyset = [s for s in cat.sets("symbols") if s.variants]
    if anyset:
        return anyset[0].key
    raise ValueError("the catalogue has no symbols sets; pass set_key explicitly")


def add_settlement(doc: MapDocument, cat: Catalog, name: str, pos,
                   kind: Literal["city", "town", "village", "castle", "fort", "ruin",
                                 "port", "temple", "mine"],
                   set_key: str | None = None, label: bool = True, seed: int = 0,
                   scale: float = 1.0) -> list[str]:
    """Symbol plus (optionally) a label just below it. Returns [symbol_id, label_id]."""
    if kind not in SETTLEMENT_KEYWORDS:
        raise ValueError(f"unknown settlement kind {kind!r}")
    key = set_key if set_key is not None else default_settlement_set(cat, kind)
    sym_id = add_symbol(doc, cat, key, pos, scale=scale, seed=seed)
    ids = [sym_id]
    if label:
        e = doc.get(sym_id)
        ly = min(e["position"].y + e["radius"] * e["scale"].y + LABEL_GAP, doc.height - 1.0)
        # labels are a separate list, so the (positional) symbol id stays valid
        ids.append(add_label(doc, name, (e["position"].x, ly),
                             preset="City" if kind == "city" else "Town"))
    return ids


# ------------------------------------------------------------------ paths & territories
def _res(prefix: str, style: str) -> str:
    return style if style.startswith("res://") else f"res://textures/{prefix}/{style}"


def add_path(doc: MapDocument, points, style: str = "path_blended", width: float = 4.0,
             color=None, roughness: float = 0.33, seed: int = 0) -> str:
    pts, _ = clamp_points(doc, points)
    if len(pts) < 2:
        raise ValueError("a path needs at least 2 distinct points")
    rng = np.random.default_rng(seed)
    kw = {}
    c = _color(color)
    if c is not None:
        kw["color"] = c
    return doc.add("path", template.path_entry(
        pts, style=_res("paths", style), width=width, roughness=roughness,
        noise_seed=int(rng.integers(0, 2**31 - 1)), **kw))


def add_territory(doc: MapDocument, points, color, opacity: float = 0.25,
                  style: str = "border_dash", width: float = 10.0,
                  smoothing: float = 0.2) -> str:
    pts, _ = clamp_points(doc, points)
    if len(pts) < 3:
        raise ValueError("a territory needs at least 3 distinct points")
    return doc.add("territory", template.territory_entry(
        pts, color=_color(color), smoothing=smoothing, opacity=opacity,
        style=_res("borders", style), width=width))
