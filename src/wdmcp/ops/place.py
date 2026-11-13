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
* Each new entry takes ``radius`` and ``offset`` from its sprite's own metadata (sets can
  define them per variant). Sprites with draw_mode "sample_color" (black-and-white art) get
  ``sample`` = the land colour under them (wdmcp.model.land_color), which Wonderdraft uses to
  tint them; other sprites keep ``sample`` None.
* ``rotation`` arguments are DEGREES, clockwise on screen, for symbols and labels alike.
  Wonderdraft stores label rotation in degrees but symbol rotation in radians
  (docs/format.md "Rotation units, GUI-pinned"), so symbols are converted on write.
"""
from __future__ import annotations

import bisect
import math
import re
from typing import Literal

import numpy as np

from wdmcp.assets.catalog import AssetNotFound, Catalog, SymbolSet
from wdmcp.codec.types import Color
from wdmcp.model import raster_spec as rs
from wdmcp.model import template
from wdmcp.model.land_color import land_color_at
from wdmcp.model.document import MapDocument
from wdmcp.ops import scatter as _sc

Point = tuple[float, float]
BASE_SCALE = 1.0
USER_BASE_SCALE = 0.25
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

# kind -> (set key, regex matched against the variant's name, fallback scale multiplier,
# target sprite height in px on screen). The sprites of a set differ a lot in size, so each
# variant is scaled to the target height (scale multiplier is used only if the image can't
# be read). Only used when no set_key is given; the keyword search above is the fallback if a
# curated set is missing from the catalogue.
CURATED_SETTLEMENTS: dict[str, tuple[str, str, float, float]] = {
    "city": ("res://sprites/symbols/colored_town", r"castle_0[1-7]", 0.3, 75.0),
    "town": ("res://sprites/symbols/colored_town", r"hamlet_(0[1-9]|1[0-2])", 0.5, 52.0),
    "village": ("res://sprites/symbols/farm_village_colored", r"village_houses_.*", 0.4, 40.0),
    "castle": ("res://sprites/symbols/inked_castles", r"castle_(medium|large)_.*", 0.8, 60.0),
    "fort": ("res://sprites/symbols/inked_castles", r"castle_small_.*", 0.7, 50.0),
    "ruin": ("res://sprites/symbols/ruins_colored", r".*", 0.3, 50.0),
    "port": ("res://sprites/symbols/colored_town", r"hamlet_.*", 0.5, 52.0),
    "temple": ("res://sprites/symbols/colored_town", r"castle_0[1-3]", 0.3, 65.0),
    "mine": ("res://sprites/symbols/rocks_and_more_colored", r"mine_.*", 0.4, 50.0),
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


def symbol_rotation(degrees: float) -> float:
    """Stored symbol rotation (radians, clockwise) for a rotation in degrees."""
    return math.radians(float(degrees))


SAMPLE_COLOR = "sample_color"
OLD_DEFAULT_RADIUS = 50.0          # what the catalogue used to report for per-variant sets
OLD_DEFAULT_OFFSET = (0.0, 0.0)


def _make_symbol(doc: MapDocument, s: SymbolSet, texture: str, pos, scale: float,
                 rotation: float, mirror: bool) -> dict:
    """``rotation`` in degrees (stored as radians). Radius/offset come from the sprite's
    metadata; sample_color sprites get the land colour under ``pos`` as ``sample``."""
    k = _base_scale(texture) * float(scale)
    m = s.meta(texture)
    sample = land_color_at(doc, pos[0], pos[1]) if m.draw_mode == SAMPLE_COLOR else None
    return template.symbol_entry(
        texture, pos, rotation=symbol_rotation(rotation), scale=(k, k),
        offset=m.offset, radius=m.radius, type=_TYPE.get(s.category, "symbol"),
        mirror=mirror, sample=sample)


def set_sample_for_sprite(doc: MapDocument, cat: Catalog, e: dict) -> None:
    """Set ``e["sample"]`` for its (new) sprite: the land colour for sample_color sprites,
    None for others. Left alone when the catalogue does not know the sprite."""
    meta_of = getattr(cat, "variant_meta", None)
    m = meta_of(str(e["texture"])) if meta_of is not None else None
    if m is not None:
        e["sample"] = (land_color_at(doc, e["position"].x, e["position"].y)
                       if m.draw_mode == SAMPLE_COLOR else None)


def recolor_symbols(doc: MapDocument, cat: Catalog, ids: list[str] | None = None,
                    overwrite: bool = False) -> dict[str, int]:
    """Repair symbols placed before per-variant metadata and sample colours were supported.

    For each symbol (all, or ``ids``) whose sprite the catalogue knows:
    * a sample_color sprite without ``sample`` (or any, with ``overwrite``) gets the land colour
      under it;
    * an entry still holding the old wrong defaults (radius 50, offset (0, 0)) gets the sprite's
      own radius and offset when its metadata differs.
    Returns counts: checked, recolored, reoffset, unknown (sprite not in the catalogue)."""
    if ids is None:
        targets = [e for _, e in doc.list("symbol")]
    else:
        targets = [doc.get(i) for i in dict.fromkeys(ids)]
        if any(i.partition(":")[0] != "symbol" for i in ids):
            raise ValueError("recolor_symbols only takes symbol ids")
    counts = {"checked": 0, "recolored": 0, "reoffset": 0, "unknown": 0}
    meta_of = getattr(cat, "variant_meta", None)
    for e in targets:
        counts["checked"] += 1
        m = meta_of(str(e["texture"])) if meta_of is not None else None
        if m is None:
            counts["unknown"] += 1
            continue
        if m.draw_mode == SAMPLE_COLOR and (overwrite or e.get("sample") is None):
            e["sample"] = land_color_at(doc, e["position"].x, e["position"].y)
            counts["recolored"] += 1
        off = e.get("offset")
        if (float(e.get("radius", 0.0)) == OLD_DEFAULT_RADIUS and off is not None
                and (off.x, off.y) == OLD_DEFAULT_OFFSET
                and (m.radius, m.offset) != (OLD_DEFAULT_RADIUS, OLD_DEFAULT_OFFSET)):
            e["radius"] = float(m.radius)
            e["offset"] = template._v(m.offset)
            counts["reoffset"] += 1
    return counts


def pack_of_texture(texture: str) -> tuple[Literal["user", "builtin"], str] | None:
    """Pack a texture belongs to: ("user", name) for ``user://assets/<pack>/...``,
    ("builtin", name) for ``res://packs/<pack>/...``, None for core assets."""
    for prefix, kind in (("user://assets/", "user"), ("res://packs/", "builtin")):
        if texture.startswith(prefix):
            name = texture[len(prefix):].split("/", 1)[0]
            return (kind, name) if name else None
    return None


def _register_packs(doc: MapDocument, entries: list[dict]) -> None:
    """Wonderdraft refuses to load pack assets the map doesn't list."""
    for e in entries:
        pk = pack_of_texture(e["texture"])
        if pk is None:
            continue
        key = "included_packs" if pk[0] == "user" else "included_default_packs"
        lst = doc.root.get(key)
        if lst is None:
            lst = doc.root[key] = []
        if pk[1] not in lst:
            lst.append(pk[1])


def _insert_symbols(doc: MapDocument, entries: list[dict]) -> list[str]:
    """Insert keeping ``(z_index, y)`` order; return ids of ``entries`` after insertion."""
    _register_packs(doc, entries)
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
    """One symbol; ``rotation`` is degrees clockwise (stored as radians)."""
    s = cat.get_set(set_key)
    x, y = _require_in_map(doc, pos, "symbol")
    rng = np.random.default_rng(seed)
    tex = _pick_variant(s, variant, rng)
    return _insert_symbols(doc, [_make_symbol(doc, s, tex, (x, y), scale, rotation, mirror)])[0]


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
        entries.append(_make_symbol(doc, s, tex, (float(x), float(y)), scale,
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
    """One label; ``rotation`` is degrees clockwise (stored as-is)."""
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
def _words(text: str) -> str:
    return re.sub(r"[_\-]+", " ", text)


def default_settlement_set(cat: Catalog, kind: str) -> str:
    """Pick a built-in symbols set for ``kind`` via SETTLEMENT_KEYWORDS."""
    if kind not in SETTLEMENT_KEYWORDS:
        raise ValueError(f"unknown settlement kind {kind!r}; expected one of "
                         f"{', '.join(SETTLEMENT_KEYWORDS)}")
    builtin = [s for s in cat.sets("symbols") if s.key.startswith("res://") and s.variants]
    for kw in SETTLEMENT_KEYWORDS[kind]:
        pat = re.compile(rf"\b{re.escape(kw)}s?\b", re.IGNORECASE)
        for s in builtin:
            if pat.search(_words(s.key.rsplit("/", 1)[-1])) or pat.search(_words(s.name)):
                return s.key
    core = [s for s in builtin if s.key.startswith("res://sprites/")]
    if core or builtin:
        return (core or builtin)[0].key
    anyset = [s for s in cat.sets("symbols") if s.variants]
    if anyset:
        return anyset[0].key
    raise ValueError("the catalogue has no symbols sets; pass set_key explicitly")


def curated_settlement(cat: Catalog, kind: str) -> tuple[str, list[int], float] | None:
    """(set key, indexes of the matching variants, scale multiplier) for ``kind``, or None
    when the curated set is missing from the catalogue or no variant name matches."""
    if kind not in CURATED_SETTLEMENTS:
        raise ValueError(f"unknown settlement kind {kind!r}")
    key, pattern, k, _ = CURATED_SETTLEMENTS[kind]
    try:
        s = cat.get_set(key)
    except AssetNotFound:
        return None
    pat = re.compile(pattern)
    idx = [i for i, v in enumerate(s.variants) if pat.fullmatch(v.rsplit("/", 1)[-1])]
    return (key, idx, k) if idx else None


def add_settlement(doc: MapDocument, cat: Catalog, name: str, pos,
                   kind: Literal["city", "town", "village", "castle", "fort", "ruin",
                                 "port", "temple", "mine"],
                   set_key: str | None = None, label: bool = True, seed: int = 0,
                   scale: float = 1.0, variant: int | None = None,
                   warnings: list[str] | None = None) -> list[str]:
    """Symbol plus (optionally) a label just below it. Returns [symbol_id, label_id].

    Without ``set_key`` the curated sprite for ``kind`` is used (see CURATED_SETTLEMENTS),
    chosen among its matching variants by ``seed`` (or ``variant``, an index into the set's
    variants); ``scale`` multiplies the kind's default size. If the curated set is missing,
    the keyword search is used and a message is appended to ``warnings``."""
    if kind not in SETTLEMENT_KEYWORDS:
        raise ValueError(f"unknown settlement kind {kind!r}")
    if set_key is None:
        cur = curated_settlement(cat, kind)
        if cur is not None:
            set_key, idx, k = cur
            if variant is None:
                variant = idx[int(np.random.default_rng(seed).integers(0, len(idx)))]
            try:
                h = cat.image(cat.get_set(set_key).variants[variant]).height
                k = CURATED_SETTLEMENTS[kind][3] / max(h, 1)
            except Exception:  # image unavailable: use the fixed multiplier
                pass
            scale = scale * k
        elif warnings is not None:
            warnings.append(f"curated {kind} set {CURATED_SETTLEMENTS[kind][0]} is not in the "
                            f"catalogue; used keyword search instead")
    key = set_key if set_key is not None else default_settlement_set(cat, kind)
    sym_id = add_symbol(doc, cat, key, pos, variant, scale=scale, seed=seed)
    ids = [sym_id]
    if label:
        e = doc.get(sym_id)
        preset = "City" if kind == "city" else "Town"
        gap = 0.35 * _label_kwargs(doc, preset)["size"]
        try:
            half = cat.image(e["texture"]).height * e["scale"].y / 2.0
        except Exception:  # image unavailable: fall back to the set radius
            half = e["radius"] * e["scale"].y
        ly = e["position"].y + e["offset"].y + half + gap
        ly = min(max(ly, 0.0), doc.height - 1.0)
        # labels are a separate list, so the (positional) symbol id stays valid
        ids.append(add_label(doc, name, (e["position"].x, ly), preset=preset))
    return ids


# ------------------------------------------------------------------ paths & territories
def _res(prefix: str, style: str) -> str:
    return style if style.startswith(("res://", "user://")) else f"res://textures/{prefix}/{style}"


def resolve_style(cat: Catalog | None, kind: str, style: str) -> str:
    """Stored texture for a path/border/frame style, validated against the catalogue's pck
    when it has one (AssetNotFound with close matches otherwise)."""
    resolver = getattr(cat, "resolve_texture", None)
    if resolver is not None:
        return resolver(kind, style)
    return style if kind == "frames" else _res(kind, style)


def add_path(doc: MapDocument, points, style: str = "path_blended", width: float = 4.0,
             color=None, roughness: float = 0.33, seed: int = 0, cat: Catalog | None = None,
             clamped: list[int] | None = None) -> str:
    """Path through ``points`` (clamped to the map). ``style`` is a texture name under
    res://textures/paths (validated when ``cat`` has a pck). The number of clamped points
    is appended to ``clamped`` when given."""
    tex = resolve_style(cat, "paths", style)
    pts, moved = clamp_points(doc, points)
    if clamped is not None:
        clamped.append(moved)
    if len(pts) < 2:
        raise ValueError("a path needs at least 2 distinct points")
    rng = np.random.default_rng(seed)
    kw = {}
    c = _color(color)
    if c is not None:
        kw["color"] = c
    return doc.add("path", template.path_entry(
        pts, style=tex, width=width, roughness=roughness,
        noise_seed=int(rng.integers(0, 2**31 - 1)), **kw))


def add_territory(doc: MapDocument, points, color, opacity: float = 0.25,
                  style: str = "border_dash", width: float = 10.0,
                  smoothing: float = 0.2, cat: Catalog | None = None,
                  clamped: list[int] | None = None) -> str:
    """Filled, bordered territory; ``opacity`` in [0, 1]; ``style`` is a texture name under
    res://textures/borders (validated when ``cat`` has a pck)."""
    if not (0.0 <= float(opacity) <= 1.0):
        raise ValueError(f"opacity must be between 0 and 1, got {opacity}")
    tex = resolve_style(cat, "borders", style)
    pts, moved = clamp_points(doc, points)
    if clamped is not None:
        clamped.append(moved)
    if len(pts) < 3:
        raise ValueError("a territory needs at least 3 distinct points")
    return doc.add("territory", template.territory_entry(
        pts, color=_color(color), smoothing=smoothing, opacity=opacity,
        style=tex, width=width))
