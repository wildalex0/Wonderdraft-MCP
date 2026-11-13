"""Wonderdraft MCP server (stdio). Tools are plain module-level functions that act on
one module-level :class:`Session`; the FastMCP registration at the bottom is thin.

stdout carries the MCP protocol only: logging goes to stderr.
"""
import copy
import io
import logging
import math
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import Image, MCPServer
from PIL import Image as PILImage
from pydantic import Field
from mcp.server.mcpserver.exceptions import ToolError

from wdmcp import convert, region
from wdmcp.assets.catalog import CATEGORIES
from wdmcp.assets.themes import apply_theme as _apply_theme
from wdmcp.codec.types import Vector2
from wdmcp.export import ExportError, export_via_app, is_supported
from wdmcp.model import raster_spec as rs
from wdmcp.model.document import MapDocument
from wdmcp.model.points import format_points, parse_points
from wdmcp.ops import place, terrain
from wdmcp.render.preview import render_preview as _render_preview
from wdmcp.session import Session, tool_errors

log = logging.getLogger("wdmcp")

Points = list[list[float]]
MAX_SEED = 2**32 - 1
Seed = Annotated[int, Field(ge=0, le=MAX_SEED, description=(
    "Random seed (0 to 2^32-1); the same seed gives the same result."))]
Color = str | list[float]
SettlementKind = Literal["city", "town", "village", "castle", "fort", "ruin", "port",
                         "temple", "mine"]
KINDS = ("symbol", "label", "path", "territory")
MAX_MAP_SIDE = 8192
MAX_PREVIEW_SIDE = 8192
MIN_MAP_SIDE = 64
ID_NOTE = ("Symbol ids are positional and shift when symbols are inserted or removed; "
           "the ids returned here are valid now.")
RASTER_TOOLS = ("mask", "ground")  # rasters the terrain ops may write
UNSAVED_WARNING = "the previously open map had unsaved changes; undo restores it"

_session = Session()


def session() -> Session:
    return _session


def reset_session(catalog=None) -> Session:
    """Replace the module session (tests inject a fake catalog)."""
    global _session
    _session = Session(catalog)
    return _session


# ----------------------------------------------------------------- helpers
def _result(ids: list[str], summary: str, **extra) -> dict:
    return {"ids": ids, "summary": summary, **extra}


def _counts(doc: MapDocument) -> dict[str, int]:
    return {k: len(doc._list(k)) for k in KINDS}


def _land_fraction(doc: MapDocument) -> float:
    return float(rs.is_land(doc.raster("mask")[..., 3]).mean())


def _detect_theme(doc: MapDocument, cat) -> str:
    """Name of the catalog theme whose values the map's theme already carries, else "custom"."""
    try:
        for name, tj in cat.themes().items():
            probe = type("P", (), {})()
            probe.root = {"theme": copy.deepcopy(doc.root["theme"])}
            _apply_theme(probe, tj)
            if probe.root["theme"] == doc.root["theme"]:
                return name
    except Exception:  # best effort only
        log.debug("theme detection failed", exc_info=True)
    return "custom"


def _packs(doc: MapDocument) -> list[str]:
    return list(doc.root.get("included_packs") or []) + list(
        doc.root.get("included_default_packs") or [])


def _packs_added(before: list[str], doc: MapDocument) -> list[str]:
    return [p for p in _packs(doc) if p not in before]


def _check_seed(seed) -> None:
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= MAX_SEED:
        raise ValueError(f"seed must be an integer from 0 to {MAX_SEED}, got {seed!r}")


def _finite(*vals: float) -> None:
    if not all(math.isfinite(v) for v in vals):
        raise ValueError("numeric arguments must be finite")


def _pos(v, what="pos") -> list[float]:
    p = convert.to_vec2(v, what)
    return [p.x, p.y]


# ----------------------------------------------------------------- lifecycle
@tool_errors
def new_map(width: int = 1920, height: int = 1080, theme: str = "Adventure") -> dict:
    """Create a blank all-water map (replacing any open map; undo brings that one back) and
    apply a theme.

    Use list_themes for theme names. Then paint land with generate_landmass or paint_land."""
    if not (MIN_MAP_SIDE <= width <= MAX_MAP_SIDE and MIN_MAP_SIDE <= height <= MAX_MAP_SIDE):
        raise ValueError(f"width and height must be between {MIN_MAP_SIDE} and {MAX_MAP_SIDE}")
    s = session()
    theme_json = s.catalog.get_theme(theme)  # raises AssetNotFound with close matches
    doc = MapDocument.new(width, height)
    _apply_theme(doc, theme_json)
    replaced = s.replace_current(doc, dirty=True, label="new_map")
    msg = f"Created blank {width}x{height} map with theme {theme!r}. It is all water."
    warnings = []
    if replaced:
        warnings.append(UNSAVED_WARNING)
        msg += " Warning: " + UNSAVED_WARNING + "."
    return _result([], msg, replaced_unsaved_map=replaced, warnings=warnings)


@tool_errors
def open_map(path: str) -> dict:
    """Open a .wonderdraft_map file. The file is only read, never modified; save_map writes.
    The previously open map is replaced (undo brings it back)."""
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        raise ValueError(f"file not found: {p}")
    doc = MapDocument.open(p)
    replaced = session().replace_current(doc, dirty=False, label="open_map")
    c = _counts(doc)
    msg = (f"Opened {p} ({doc.width}x{doc.height}): " +
           ", ".join(f"{n} {k}" for k, n in c.items()) + " objects.")
    warnings = []
    if replaced:
        warnings.append(UNSAVED_WARNING)
        msg += " Warning: " + UNSAVED_WARNING + "."
    return _result([], msg, counts=c, path=str(p), replaced_unsaved_map=replaced,
                   warnings=warnings)


@tool_errors
def save_map(path: str | None = None) -> dict:
    """Save the open map. Without a path, overwrite the file it was opened from. New maps
    need a path. Before an existing file is overwritten a timestamped .bak copy is made, at
    most once per file per session and never of a file this server wrote itself. Returns the
    absolute path and the backup (or null)."""
    s = session()
    doc = s.doc()
    target = Path(path).expanduser().resolve() if path else doc.path
    if target is None:
        raise ValueError("this map has not been saved yet; pass a path to save_map")
    if target.suffix == "":
        target = target.with_name(target.name + ".wonderdraft_map")
    bak = doc.save(target)
    saved = doc.path
    s.dirty = False
    msg = f"Saved {saved}."
    if bak is not None:
        msg += f" Previous file backed up to {bak.name}."
    return {"path": str(saved), "backup": str(bak) if bak else None, "summary": msg}


@tool_errors
def map_summary() -> dict:
    """Size, theme, object counts, land fraction, included packs, file path and unsaved flag."""
    s = session()
    doc = s.doc()
    c = _counts(doc)
    land = _land_fraction(doc)
    return {
        "width": doc.width, "height": doc.height,
        "theme": _detect_theme(doc, s.catalog),
        "counts": c, "land_fraction": round(land, 4),
        "included_packs": list(doc.root.get("included_packs") or []),
        "included_default_packs": list(doc.root.get("included_default_packs") or []),
        "path": str(doc.path) if doc.path else None,
        "dirty": s.dirty,
        "undo_depth": len(s.history),
    }


# ----------------------------------------------------------------- discovery
@tool_errors
def list_themes() -> dict:
    """Names of the built-in themes usable with new_map / apply_theme."""
    return {"themes": sorted(session().catalog.themes())}


@tool_errors
def list_asset_sets(category: str | None = None, query: str | None = None,
                    limit: int = 50) -> dict:
    """List symbol sets: key (pass to add_symbol etc.), name, category
    (symbols|mountains|trees), pack and variant count. query matches key or name."""
    if category is not None and category not in CATEGORIES:
        raise ValueError(f"unknown category {category!r}; use one of {', '.join(CATEGORIES)}")
    rows = session().catalog.sets(category, query)
    out = [{"key": r.key, "name": r.name, "category": r.category, "pack": r.pack,
            "variants": len(r.variants)} for r in rows[:max(limit, 0)]]
    return {"total": len(rows), "sets": out}


@tool_errors
def list_objects(kind: Literal["symbol", "label", "path", "territory"],
                 bbox: list[float] | None = None, limit: int = 200) -> dict:
    """List objects of one kind, optionally inside bbox [x0, y0, x1, y1] (map pixels)."""
    doc = session().doc()
    box = None
    if bbox is not None:
        if len(bbox) != 4:
            raise ValueError("bbox must be [x0, y0, x1, y1]")
        box = tuple(float(v) for v in bbox)
    rows = []
    found = doc.list(kind, box)
    for id_, e in found[:max(limit, 0)]:
        if kind == "symbol":
            base = place._base_scale(str(e["texture"]))
            rows.append({"id": id_, "texture": e["texture"],
                         "position": [e["position"].x, e["position"].y],
                         "scale": round(e["scale"].x / base, 4)})
        elif kind == "label":
            rows.append({"id": id_, "text": e["text"],
                         "position": [e["position"].x, e["position"].y]})
        else:
            pts = parse_points(e["points"])
            rows.append({"id": id_, "style": e["style"], "n_points": len(pts),
                         "start": list(pts[0]) if pts else None,
                         "end": list(pts[-1]) if pts else None})
    return {"kind": kind, "count": len(found), "objects": rows}


# ----------------------------------------------------------------- terrain
def _terrain(label: str, fn, summary: str) -> dict:
    s = session()
    with s.mutation(label, RASTER_TOOLS) as doc:
        n = fn(doc)
    return _result([], summary.format(n=n), pixels_changed=n)


@tool_errors
def paint_land(polygon: Points, mode: Literal["add", "subtract"] = "add",
               roughness: float = 0.5, seed: Seed = 0) -> dict:
    """Paint (add) or erase (subtract) land inside a polygon [[x, y], ...] with a natural,
    noise-displaced coastline. roughness 0 gives straight edges."""
    _check_seed(seed)
    pts = convert.to_points(polygon, "polygon")
    return _terrain("paint_land", lambda d: terrain.paint_land(d, pts, mode, roughness, seed),
                    f"{mode} land: {{n}} pixels changed.")


@tool_errors
def generate_landmass(coverage: float = 0.4, seed: Seed = 0,
                      kind: Literal["continent", "archipelago", "island"] = "continent") -> dict:
    """Replace the whole map's terrain with generated land; coverage is the land fraction (0-1)."""
    _check_seed(seed)
    return _terrain("generate_landmass",
                    lambda d: terrain.generate_landmass(d, coverage, seed, kind),
                    f"Generated a {kind} (coverage {coverage}): {{n}} pixels changed.")


@tool_errors
def raise_area(polygon: Points, amount: float) -> dict:
    """Raise (or lower, if negative) land height inside a polygon. amount is in land-height
    units: land height runs from 0 (sea level) to 1 (highest), so 0.1 raises by a tenth of the
    full range; results are clamped to 0-1. Water is untouched and never becomes land."""
    pts = convert.to_points(polygon, "polygon")
    return _terrain("raise_area", lambda d: terrain.raise_area(d, pts, amount),
                    "Adjusted land height: {n} pixels changed.")


@tool_errors
def add_lake(polygon: Points, roughness: float = 0.3, seed: Seed = 0) -> dict:
    """Add a freshwater lake inside a polygon (only shows on land)."""
    _check_seed(seed)
    pts = convert.to_points(polygon, "polygon")
    return _terrain("add_lake", lambda d: terrain.add_lake(d, pts, roughness, seed),
                    "Added lake: {n} pixels changed.")


@tool_errors
def add_river(points: Points, width: float = 3.0, seed: Seed = 0) -> dict:
    """Add a meandering river through points (first point is the source; end it in the sea
    or a lake); drawn on land only. width is in px at full width; the river narrows towards
    its source over its first few widths."""
    _check_seed(seed)
    pts = convert.to_points(points)
    return _terrain("add_river", lambda d: terrain.add_river(d, pts, width, seed),
                    "Added river: {n} pixels changed.")


@tool_errors
def paint_ground(polygon: Points, color_index: int, softness: float = 0.5) -> dict:
    """Paint theme ground colour number color_index (0-based) over a polygon, feathered by
    softness 0-1."""
    pts = convert.to_points(polygon, "polygon")
    return _terrain("paint_ground", lambda d: terrain.paint_ground(d, pts, color_index, softness),
                    "Painted ground colour: {n} pixels changed.")


# ----------------------------------------------------------------- placement
def _symbols(label: str, fn, what=None) -> dict:
    """Run a symbol-placing op; fn(doc, cat) -> ids or (ids, dropped). ``what(ids)`` replaces
    the default "Placed N symbol(s)" opening of the summary."""
    s = session()
    with s.mutation(label) as doc:
        before = _packs(doc)
        out = fn(doc, s.catalog)
        added = _packs_added(before, doc)
    ids, dropped = out if isinstance(out, tuple) else (out, None)
    extra: dict[str, Any] = {"packs_registered": added}
    if dropped is not None:
        extra["dropped"] = dropped
    msg = what(ids) if what else f"Placed {len(ids)} symbol(s)"
    if dropped:
        msg += f", dropped {dropped} (off-map or on water)"
    msg += ". " + ID_NOTE
    if added:
        msg += f" Registered pack(s) in the map: {', '.join(added)}."
    return _result(ids, msg, **extra)


@tool_errors
def add_symbol(set_key: str, pos: list[float], variant: int | None = None, scale: float = 1.0,
               rotation: float = 0.0, mirror: bool = False, seed: Seed = 0) -> dict:
    """Place one symbol from a set (key from list_asset_sets) at pos [x, y]. variant picks a
    sprite by index, otherwise it is random. scale multiplies the set's base size (1 = the
    size Wonderdraft places it at). rotation is in degrees, clockwise."""
    _check_seed(seed)
    p = _pos(pos)
    return _symbols("add_symbol", lambda d, c: [place.add_symbol(
        d, c, set_key, p, variant, scale, rotation, mirror, seed)])


@tool_errors
def scatter_symbols(set_key: str, polygon: Points, density: float, min_spacing: float | None = None,
                    land_only: bool = True, seed: Seed = 0, scale: float = 1.0) -> dict:
    """Scatter symbols (Poisson-disk) inside a polygon. density is symbols per 100x100 px of
    area (min_spacing in px, default from the set's radius, may lower the count). scale
    multiplies the set's base size. Returns the placed ids and how many samples were dropped
    (off-map, or on water with land_only)."""
    _check_seed(seed)
    pts = convert.to_points(polygon, "polygon")
    return _symbols("scatter_symbols", lambda d, c: place.scatter(
        d, c, set_key, pts, density, min_spacing, land_only, seed, scale))


@tool_errors
def add_mountain_range(set_key: str, polyline: Points, width: float, density: float,
                       seed: Seed = 0) -> dict:
    """Place mountains in a ribbon of the given width (px) along a polyline (land only).
    density is symbols per 100x100 px of ribbon area."""
    _check_seed(seed)
    line = convert.to_points(polyline, "polyline")
    return _symbols("add_mountain_range", lambda d, c: place.mountain_range(
        d, c, set_key, line, width, density, seed))


@tool_errors
def add_forest(set_key: str, polygon: Points, density: float, seed: Seed = 0) -> dict:
    """Fill a polygon with trees from a tree set (land only). density is trees per
    100x100 px of area."""
    _check_seed(seed)
    pts = convert.to_points(polygon, "polygon")
    return _symbols("add_forest", lambda d, c: place.forest(d, c, set_key, pts, density, seed))


@tool_errors
def add_settlement(name: str, pos: list[float], kind: SettlementKind, set_key: str | None = None,
                   label: bool = True, seed: Seed = 0, scale: float = 1.0,
                   variant: int | None = None) -> dict:
    """Place a settlement symbol and (optionally) its name label just below it. Returns
    [symbol_id, label_id]. Each kind has a curated default sprite (seed picks among its
    variants); set_key overrides the set and variant picks one sprite by index. scale
    multiplies the kind's default size."""
    _check_seed(seed)
    p = _pos(pos)

    def what(ids):
        return (f"Placed {kind} {name!r}: symbol {ids[0]}" +
                (f" with its name label {ids[1]}" if label else ""))

    return _symbols("add_settlement", lambda d, c: place.add_settlement(
        d, c, name, p, kind, set_key, label, seed, scale, variant), what)


@tool_errors
def add_label(text: str, pos: list[float], preset: str = "Town", size: int | None = None,
              rotation: float = 0.0, curve: float = 0.0, color: Color | None = None) -> dict:
    """Add a text label using one of the theme's label presets (Town, City, Region, ...;
    preset_used says which one matched). color is "#rrggbb" or [r, g, b(, a)] floats 0-1
    (0-255 values are rejected with a hint). size is the font size in px (default from the
    preset); rotation is in degrees, clockwise (90 = vertical); curve is Wonderdraft's label
    curve value stored as-is (0 = straight; non-zero bends the text along an arc, the sign
    picks the direction; start small and check in Wonderdraft, the preview draws it straight)."""
    p = _pos(pos)
    col = convert.to_color(color) if color is not None else None
    s = session()
    with s.mutation("add_label") as doc:
        used = place.label_preset_used(doc, preset)
        id_ = place.add_label(doc, text, p, preset, size, rotation, curve, col)
    return _result([id_], f"Added label {text!r} using preset {used!r}.", preset_used=used)


@tool_errors
def add_path(points: Points, style: str = "path_blended", width: float = 4.0,
             color: Color | None = None, roughness: float = 0.33, seed: Seed = 0) -> dict:
    """Add a road/path along points; points outside the map are clamped to its edge
    (clamped = how many). style is one of Wonderdraft's path textures: path_blended,
    path_circle, path_dash, path_dash_dot, path_dash_dot_dot, path_directional,
    path_double_paired, path_hash_marks, path_solid, path_solid_outlined. width in px;
    roughness is the wobble (0 = smooth, ~0.3 default)."""
    _check_seed(seed)
    pts = convert.to_points(points)
    col = convert.to_color(color) if color is not None else None
    s = session()
    clamped: list[int] = []
    with s.mutation("add_path") as doc:
        id_ = place.add_path(doc, pts, style, width, col, roughness, seed, cat=s.catalog,
                             clamped=clamped)
    msg = f"Added path with {len(pts)} points."
    if clamped[0]:
        msg += f" {clamped[0]} point(s) outside the map were clamped to its edge."
    return _result([id_], msg, clamped=clamped[0])


@tool_errors
def add_territory(points: Points, color: Color, opacity: float = 0.25,
                  style: str = "border_dash", width: float = 10.0, smoothing: float = 0.2) -> dict:
    """Add a filled, bordered territory polygon; points outside the map are clamped
    (clamped = how many). color is "#rrggbb" or [r, g, b(, a)] floats 0-1 (0-255 values are
    rejected with a hint). opacity is the fill opacity 0-1. style is one of Wonderdraft's
    border textures: border_dark_dot, border_dash, border_gradient, border_solid. width is
    the border width in px."""
    pts = convert.to_points(points)
    col = convert.to_color(color)
    s = session()
    clamped: list[int] = []
    with s.mutation("add_territory") as doc:
        id_ = place.add_territory(doc, pts, col, opacity, style, width, smoothing,
                                  cat=s.catalog, clamped=clamped)
    msg = f"Added territory with {len(pts)} points."
    if clamped[0]:
        msg += f" {clamped[0]} point(s) outside the map were clamped to its edge."
    return _result([id_], msg, clamped=clamped[0])


# ----------------------------------------------------------------- editing
@tool_errors
def update_object(id: str, fields: dict) -> dict:
    """Update fields of one object (id like "label:3", see list_objects). Values are checked
    against the type Wonderdraft stores for each key; unknown keys are rejected with the valid
    list.
    - rotation: degrees, clockwise, for symbols and labels alike (symbols are stored in radians).
    - scale (symbols): a number or [x, y], a multiplier on the set's base size, the same meaning
      as add_symbol's scale (list_objects shows the current multiplier).
    - variant (symbols): sprite index within the symbol's set (or within the set given as
      texture in the same call); texture: a sprite path. Packs are registered as needed.
    - style (paths/territories): a path or border texture name as in add_path / add_territory.
    - Vectors (position, offset) are [x, y]; colours are [r, g, b(, a)] floats 0-1 or
      "#rrggbb"; points (path/territory) are [[x, y], ...]."""
    s = session()
    doc = s.doc()
    entry = doc.get(id)  # validates the id
    kind = id.partition(":")[0]
    conv = convert.convert_fields(kind, fields, entry, s.catalog)
    with s.mutation("update_object") as doc:
        before = _packs(doc)
        e = doc.update(id, **conv)
        if "texture" in conv:
            place._register_packs(doc, [e])
            if "sample" not in fields:  # the tint follows the new sprite's draw mode
                place.set_sample_for_sprite(doc, s.catalog, e)
        added = _packs_added(before, doc)
    msg = f"Updated {id}: {', '.join(conv) or 'nothing'}."
    if added:
        msg += f" Registered pack(s) in the map: {', '.join(added)}."
    return _result([id], msg, packs_registered=added)


@tool_errors
def move_objects(ids: list[str], dx: float, dy: float) -> dict:
    """Shift objects by (dx, dy) map pixels. Symbols/labels move their position; paths and
    territories move every point."""
    _finite(dx, dy)
    s = session()
    doc = s.doc()
    uniq = list(dict.fromkeys(ids))
    for i in uniq:
        doc.get(i)
    with s.mutation("move_objects") as doc:
        for i in uniq:
            e = doc.get(i)
            if i.partition(":")[0] in ("symbol", "label"):
                e["position"] = Vector2(e["position"].x + dx, e["position"].y + dy)
            else:
                e["points"] = format_points([(x + dx, y + dy) for x, y in parse_points(e["points"])])
    return _result(uniq, f"Moved {len(uniq)} object(s) by ({dx}, {dy}).")


@tool_errors
def remove_objects(ids: list[str]) -> dict:
    """Delete objects. All ids refer to the state before the call; later ids of the same kind
    shift down afterwards, so re-list before further edits."""
    s = session()
    with s.mutation("remove_objects") as doc:
        n = doc.remove(list(ids))
    return _result([], f"Removed {n} object(s). Remaining ids of those kinds may have shifted; "
                       f"use list_objects to get current ids.")


@tool_errors
def recolor_symbols(ids: list[str] | None = None, overwrite: bool = False) -> dict:
    """Repair symbols placed by older versions of this server (or by hand-edited files) so
    Wonderdraft draws them like its own: black-and-white "paintable" (sample_color) sprites
    get the land colour under them as their tint (only where it is missing, or for all of
    them with overwrite=true), and entries still holding the old wrong default radius 50 /
    offset (0, 0) get their sprite's own values. ids limits it to some symbols (default all).
    New placements never need this. Undoable."""
    s = session()
    s.doc()
    with s.mutation("recolor_symbols") as doc:
        c = place.recolor_symbols(doc, s.catalog, ids, overwrite)
    msg = (f"Checked {c['checked']} symbol(s): recoloured {c['recolored']}, restored radius/offset "
           f"of {c['reoffset']}.")
    if c["unknown"]:
        msg += f" {c['unknown']} use sprites not in the asset catalogue and were left alone."
    return _result([], msg, **c)


@tool_errors
def apply_theme(name: str) -> dict:
    """Apply a built-in theme (colours, textures, label presets) to the open map."""
    s = session()
    s.doc()
    theme_json = s.catalog.get_theme(name)
    with s.mutation("apply_theme") as doc:
        _apply_theme(doc, theme_json)
    return _result([], f"Applied theme {name!r}.")


@tool_errors
def set_frame(enabled: bool, texture: str | None = None, size: float | None = None) -> dict:
    """Turn the decorative map frame on/off; texture and size are optional. texture is one
    of Wonderdraft's frames: celtic_dotted, celtic_leaves, celtic_woven_1, celtic_woven_2,
    checkered_opaque, checkered_transparent, double_pen_opaque, double_pen_transparent,
    double_pen_vignette, entwined_corner_opaque, entwined_corner_transparent, hatch_opaque,
    hatch_transparent, old_ink_opaque, old_ink_transparent, solid_line_opaque,
    solid_line_tansparent (sic)."""
    s = session()
    tex = place.resolve_style(s.catalog, "frames", texture) if texture is not None else None
    with s.mutation("set_frame") as doc:
        f = doc.root["frame"]
        f["enabled"] = bool(enabled)
        if tex is not None:
            f["texture"] = tex
        if size is not None:
            _finite(size)
            f["size"] = float(size)
    return _result([], f"Frame {'enabled' if enabled else 'disabled'} "
                       f"(texture {f['texture']!r}, size {f['size']}).")


# ----------------------------------------------------------------- one-shot build
@tool_errors
def build_region(spec: region.RegionSpec, out_path: str) -> dict:
    """Build a complete first-draft map from a RegionSpec (see region_spec_schema), save it
    to out_path and make it the open map, so the edit tools and render_preview work next.
    Order: land, mountains, rivers/lakes, ground, forests, settlements, roads, realms, labels.
    A settlement on water is moved to land within 60 px (see warnings)."""
    s = session()
    cat = s.catalog
    res = region.build(spec, cat)
    target = Path(out_path).expanduser().resolve()
    if target.suffix == "":
        target = target.with_name(target.name + ".wonderdraft_map")
    bak = res.doc.save(target)
    saved = res.doc.path
    replaced = s.replace_current(res.doc, dirty=False, label="build_region")
    if replaced:
        res.warnings.append(UNSAVED_WARNING)
    c = res.counts
    msg = (f"Built {spec.name!r} ({spec.width}x{spec.height}, theme {spec.theme!r}) and saved it to "
           f"{saved}: {c['settlements']} settlements, {c['realms']} realms, {c['roads']} roads, "
           f"{c['rivers']} rivers, {c['lakes']} lakes, {c['mountain_symbols']} mountain symbols, "
           f"{c['forest_symbols']} forest symbols, {c['labels']} labels. "
           f"It is the open map: call render_preview to see it, then refine with the edit tools.")
    if bak is not None:
        msg += f" The existing file was backed up to {bak.name}."
    if res.warnings:
        msg += " Warnings: " + "; ".join(res.warnings) + "."
    msg += (" Returned ids cover labels, paths, territories and settlement symbols; "
            "use list_objects for mountain and forest symbols. " + ID_NOTE)
    return {"ids": res.ids, "summary": msg, "counts": c, "warnings": res.warnings,
            "path": str(saved), "backup": str(bak) if bak else None,
            "replaced_unsaved_map": replaced}


def region_spec_schema() -> dict:
    """JSON Schema of the RegionSpec that build_region takes (field meanings included)."""
    return region.RegionSpec.model_json_schema()


# ----------------------------------------------------------------- output
@tool_errors
def render_preview(scale: float = 0.5, bbox: list[int] | None = None,
                   show_ids: bool = False) -> list:
    """Render the map to a PNG you can see (also saved to disk; the path is in the text).
    scale is output px per map px (the output is capped at 8192 px on its longest side).
    bbox [x0, y0, x1, y1] in map pixels zooms in; show_ids tags objects with their ids."""
    s = session()
    doc = s.doc()
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError("scale must be a positive number")
    box = None
    if bbox is not None:
        if len(bbox) != 4:
            raise ValueError("bbox must be [x0, y0, x1, y1]")
        box = tuple(int(v) for v in bbox)
    bw, bh = (box[2] - box[0], box[3] - box[1]) if box else (doc.width, doc.height)
    note = ""
    if max(bw, bh) * scale > MAX_PREVIEW_SIDE and max(bw, bh) > 0:
        scale = MAX_PREVIEW_SIDE / max(bw, bh)
        note = f" Scale was capped at {scale:.4g} to keep the image within {MAX_PREVIEW_SIDE} px."
    img = _render_preview(doc, s.catalog, scale=scale, bbox=box, show_ids=show_ids)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    data = buf.getvalue()
    path = s.preview_path(doc)
    try:
        path.write_bytes(data)
    except OSError:
        path = s.temp_preview_path()
        path.write_bytes(data)
    text = f"Preview {img.width}x{img.height} (scale {scale:.4g}).{note} Saved: {path}"
    return [Image(data=data, format="png"), text]


@tool_errors
def export_image(path: str, format: Literal["png", "jpg"] = "png",
                 via: Literal["wonderdraft", "preview"] = "wonderdraft") -> dict:
    """Export the open map as an image file at path (never overwrites an existing file; a
    .png/.jpg suffix must match format, and is added if missing).
    via="wonderdraft" drives the real Wonderdraft app (Windows only) and takes about 15-20 s:
    close Wonderdraft first and do not type meanwhile. It needs the map saved with no
    unsaved edits. If automation is unavailable or fails, nothing is written and the result has
    via="instructions" with manual steps (open the map, press Ctrl+E) in the summary.
    via="preview" writes a full-scale render from this server instead."""
    s = session()
    doc = s.doc()
    out = Path(path).expanduser().resolve()
    suffixes = {"png": (".png",), "jpg": (".jpg", ".jpeg")}[format]
    if out.suffix == "":
        out = out.with_name(out.name + suffixes[0])
    elif out.suffix.lower() not in suffixes:
        raise ValueError(f"the file suffix {out.suffix!r} does not match format={format!r}; "
                         f"use a {suffixes[0]} name or change format")
    if out.exists():
        raise ValueError(f"{out} already exists; choose a new file name (nothing was overwritten)")
    if not out.parent.is_dir():
        raise ValueError(f"folder not found: {out.parent}")
    note = ""
    if via == "preview":
        img = _render_preview(doc, s.catalog, scale=1.0)
    else:
        if doc.path is None or s.dirty:
            raise ValueError("via=\"wonderdraft\" exports the saved map file: call save_map first "
                             "(the map is unsaved or has unsaved edits). Nothing was written.")
        manual = (f"To export by hand: open {doc.path} in Wonderdraft, press Ctrl+E, keep the "
                  f"defaults and save the PNG. Or call export_image with via=\"preview\" for a "
                  f"full-scale render from this server.")
        if not is_supported():
            return _result([], "Automated export needs Wonderdraft installed on Windows "
                               "(set WDMCP_EXE if it is elsewhere). " + manual,
                           path=None, via="instructions", width=None, height=None)
        tmp_dir = Path(tempfile.mkdtemp(prefix=".wdmcp-export-", dir=out.parent))
        try:
            png = out if format == "png" else tmp_dir / "export.png"
            try:
                export_via_app(doc.path, png)
            except ExportError as e:
                if png == out:
                    png.unlink(missing_ok=True)  # only a partial file we asked Wonderdraft to make
                return _result([], f"Automated export failed: {e} " + manual,
                               path=None, via="instructions", width=None, height=None)
            img = PILImage.open(png)
            img.load()
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        if format == "jpg":
            note = " Wonderdraft exported a PNG, which was converted to JPG (quality 92)."
    if format == "jpg":
        img = img.convert("RGB")
        img.save(out, format="JPEG", quality=92)
    elif via == "preview":
        img.save(out, format="PNG")
    return _result([], f"Exported {img.width}x{img.height} {format.upper()} to {out} via {via}." + note,
                   path=str(out), via=via, width=img.width, height=img.height)


@tool_errors
def undo() -> dict:
    """Undo the last change (up to 20 steps). After new_map, open_map or build_region, undo
    first brings back the map that was open before (with its own undo history)."""
    s = session()
    label = s.undo()
    return _result([], f"Undid {label}. {len(s.history)} undo step(s) left. "
                       f"Object ids may have changed; use list_objects.")


# ----------------------------------------------------------------- registration
mcp = MCPServer("wonderdraft")

TOOLS = [
    new_map, open_map, save_map, map_summary,
    list_themes, list_asset_sets, list_objects,
    paint_land, generate_landmass, raise_area, add_lake, add_river, paint_ground,
    add_symbol, scatter_symbols, add_mountain_range, add_forest, add_settlement,
    add_label, add_path, add_territory,
    update_object, move_objects, remove_objects, recolor_symbols, apply_theme, set_frame,
    build_region, region_spec_schema,
    render_preview, export_image, undo,
]
for _fn in TOOLS:
    mcp.tool()(_fn)


def main() -> None:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    mcp.run()


if __name__ == "__main__":
    main()
