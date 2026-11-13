"""Wonderdraft MCP server (stdio). Tools are plain module-level functions that act on
one module-level :class:`Session`; the FastMCP registration at the bottom is thin.

stdout carries the MCP protocol only: logging goes to stderr.
"""
import copy
import io
import logging
import math
import sys
from pathlib import Path
from typing import Any, Literal

from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from wdmcp import convert, region
from wdmcp.assets.catalog import CATEGORIES
from wdmcp.assets.themes import apply_theme as _apply_theme
from wdmcp.codec.types import Vector2
from wdmcp.model import raster_spec as rs
from wdmcp.model.document import MapDocument
from wdmcp.model.points import format_points, parse_points
from wdmcp.ops import place, terrain
from wdmcp.render.preview import render_preview as _render_preview
from wdmcp.session import Session, tool_errors

log = logging.getLogger("wdmcp")

Points = list[list[float]]
Color = str | list[float]
SettlementKind = Literal["city", "town", "village", "castle", "fort", "ruin", "port",
                         "temple", "mine"]
KINDS = ("symbol", "label", "path", "territory")
MAX_MAP_SIDE = 8192
MIN_MAP_SIDE = 64
ID_NOTE = ("Symbol ids are positional and shift when symbols are inserted or removed; "
           "the ids returned here are valid now.")
RASTER_TOOLS = ("mask", "ground")  # rasters the terrain ops may write

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


def _finite(*vals: float) -> None:
    if not all(math.isfinite(v) for v in vals):
        raise ValueError("numeric arguments must be finite")


def _pos(v, what="pos") -> list[float]:
    p = convert.to_vec2(v, what)
    return [p.x, p.y]


# ----------------------------------------------------------------- lifecycle
@tool_errors
def new_map(width: int = 1920, height: int = 1080, theme: str = "Adventure") -> dict:
    """Create a blank all-water map (replacing any open map) and apply a theme.

    Use list_themes for theme names. Then paint land with generate_landmass or paint_land."""
    if not (MIN_MAP_SIDE <= width <= MAX_MAP_SIDE and MIN_MAP_SIDE <= height <= MAX_MAP_SIDE):
        raise ValueError(f"width and height must be between {MIN_MAP_SIDE} and {MAX_MAP_SIDE}")
    s = session()
    theme_json = s.catalog.get_theme(theme)  # raises AssetNotFound with close matches
    discarded = s.current is not None and s.dirty
    doc = MapDocument.new(width, height)
    _apply_theme(doc, theme_json)
    s.set_current(doc, dirty=True)
    msg = f"Created blank {width}x{height} map with theme {theme!r}. It is all water."
    if discarded:
        msg += " The previously open map had unsaved changes and was discarded."
    return _result([], msg)


@tool_errors
def open_map(path: str) -> dict:
    """Open a .wonderdraft_map file. The file is only read, never modified; save_map writes."""
    p = Path(path).expanduser()
    if not p.is_file():
        raise ValueError(f"file not found: {p}")
    doc = MapDocument.open(p)
    session().set_current(doc, dirty=False)
    c = _counts(doc)
    return _result([], f"Opened {p} ({doc.width}x{doc.height}): " +
                   ", ".join(f"{n} {k}" for k, n in c.items()) + " objects.", counts=c)


@tool_errors
def save_map(path: str | None = None) -> dict:
    """Save the open map. Without a path, overwrite the file it was opened from (a
    timestamped .bak of the old file is made first). New maps need a path."""
    s = session()
    doc = s.doc()
    target = Path(path).expanduser() if path else doc.path
    if target is None:
        raise ValueError("this map has not been saved yet; pass a path to save_map")
    if target.suffix == "":
        target = target.with_name(target.name + ".wonderdraft_map")
    before = set(target.parent.glob(f"{target.stem}.*.bak")) if target.parent.is_dir() else set()
    saved = doc.save(target)
    new_bak = sorted(set(saved.parent.glob(f"{saved.stem}.*.bak")) - before)
    s.dirty = False
    msg = f"Saved {saved}."
    if new_bak:
        msg += f" Previous file backed up to {new_bak[-1].name}."
    return {"path": str(saved), "backup": str(new_bak[-1]) if new_bak else None, "summary": msg}


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
            rows.append({"id": id_, "texture": e["texture"],
                         "position": [e["position"].x, e["position"].y]})
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
               roughness: float = 0.5, seed: int = 0) -> dict:
    """Paint (add) or erase (subtract) land inside a polygon [[x, y], ...] with a natural,
    noise-displaced coastline. roughness 0 gives straight edges."""
    pts = convert.to_points(polygon, "polygon")
    return _terrain("paint_land", lambda d: terrain.paint_land(d, pts, mode, roughness, seed),
                    f"{mode} land: {{n}} pixels changed.")


@tool_errors
def generate_landmass(coverage: float = 0.4, seed: int = 0,
                      kind: Literal["continent", "archipelago", "island"] = "continent") -> dict:
    """Replace the whole map's terrain with generated land; coverage is the land fraction (0-1)."""
    return _terrain("generate_landmass",
                    lambda d: terrain.generate_landmass(d, coverage, seed, kind),
                    f"Generated a {kind} (coverage {coverage}): {{n}} pixels changed.")


@tool_errors
def raise_area(polygon: Points, amount: float) -> dict:
    """Raise (or lower, if negative) land height inside a polygon. Water is untouched."""
    pts = convert.to_points(polygon, "polygon")
    return _terrain("raise_area", lambda d: terrain.raise_area(d, pts, amount),
                    "Adjusted land height: {n} pixels changed.")


@tool_errors
def add_lake(polygon: Points, roughness: float = 0.3, seed: int = 0) -> dict:
    """Add a freshwater lake inside a polygon (only shows on land)."""
    pts = convert.to_points(polygon, "polygon")
    return _terrain("add_lake", lambda d: terrain.add_lake(d, pts, roughness, seed),
                    "Added lake: {n} pixels changed.")


@tool_errors
def add_river(points: Points, width: float = 3.0, seed: int = 0) -> dict:
    """Add a meandering river through points (first point is the source); land only."""
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
def _symbols(label: str, fn) -> dict:
    """Run a symbol-placing op; fn(doc, cat) -> ids or (ids, dropped)."""
    s = session()
    with s.mutation(label) as doc:
        before = _packs(doc)
        out = fn(doc, s.catalog)
        added = _packs_added(before, doc)
    ids, dropped = out if isinstance(out, tuple) else (out, None)
    extra: dict[str, Any] = {"packs_registered": added}
    if dropped is not None:
        extra["dropped"] = dropped
    msg = f"Placed {len(ids)} symbol(s)"
    if dropped:
        msg += f", dropped {dropped} (off-map or on water)"
    msg += ". " + ID_NOTE
    if added:
        msg += f" Registered pack(s) in the map: {', '.join(added)}."
    return _result(ids, msg, **extra)


@tool_errors
def add_symbol(set_key: str, pos: list[float], variant: int | None = None, scale: float = 1.0,
               rotation: float = 0.0, mirror: bool = False, seed: int = 0) -> dict:
    """Place one symbol from a set (key from list_asset_sets) at pos [x, y]. variant picks a
    sprite by index, otherwise it is random."""
    p = _pos(pos)
    return _symbols("add_symbol", lambda d, c: [place.add_symbol(
        d, c, set_key, p, variant, scale, rotation, mirror, seed)])


@tool_errors
def scatter_symbols(set_key: str, polygon: Points, density: float, min_spacing: float | None = None,
                    land_only: bool = True, seed: int = 0, scale: float = 1.0) -> dict:
    """Scatter symbols (Poisson-disk) inside a polygon. Returns the placed ids and how many
    samples were dropped."""
    pts = convert.to_points(polygon, "polygon")
    return _symbols("scatter_symbols", lambda d, c: place.scatter(
        d, c, set_key, pts, density, min_spacing, land_only, seed, scale))


@tool_errors
def add_mountain_range(set_key: str, polyline: Points, width: float, density: float,
                       seed: int = 0) -> dict:
    """Place mountains in a ribbon of the given width (px) along a polyline (land only)."""
    line = convert.to_points(polyline, "polyline")
    return _symbols("add_mountain_range", lambda d, c: place.mountain_range(
        d, c, set_key, line, width, density, seed))


@tool_errors
def add_forest(set_key: str, polygon: Points, density: float, seed: int = 0) -> dict:
    """Fill a polygon with trees from a tree set (land only)."""
    pts = convert.to_points(polygon, "polygon")
    return _symbols("add_forest", lambda d, c: place.forest(d, c, set_key, pts, density, seed))


@tool_errors
def add_settlement(name: str, pos: list[float], kind: SettlementKind, set_key: str | None = None,
                   label: bool = True, seed: int = 0, scale: float = 1.0) -> dict:
    """Place a settlement symbol and (optionally) its name label just below it. Returns
    [symbol_id, label_id]. set_key overrides the default symbol for the kind."""
    p = _pos(pos)
    return _symbols("add_settlement", lambda d, c: place.add_settlement(
        d, c, name, p, kind, set_key, label, seed, scale))


@tool_errors
def add_label(text: str, pos: list[float], preset: str = "Town", size: int | None = None,
              rotation: float = 0.0, curve: float = 0.0, color: Color | None = None) -> dict:
    """Add a text label using one of the theme's label presets (Town, City, Region, ...;
    preset_used says which one matched)."""
    p = _pos(pos)
    col = convert.to_color(color) if color is not None else None
    s = session()
    with s.mutation("add_label") as doc:
        used = place.label_preset_used(doc, preset)
        id_ = place.add_label(doc, text, p, preset, size, rotation, curve, col)
    return _result([id_], f"Added label {text!r} using preset {used!r}.", preset_used=used)


@tool_errors
def add_path(points: Points, style: str = "path_blended", width: float = 4.0,
             color: Color | None = None, roughness: float = 0.33, seed: int = 0) -> dict:
    """Add a road/path along points; points outside the map are clamped to its edge."""
    pts = convert.to_points(points)
    col = convert.to_color(color) if color is not None else None
    s = session()
    with s.mutation("add_path") as doc:
        id_ = place.add_path(doc, pts, style, width, col, roughness, seed)
    return _result([id_], f"Added path with {len(pts)} points.")


@tool_errors
def add_territory(points: Points, color: Color, opacity: float = 0.25,
                  style: str = "border_dash", width: float = 10.0, smoothing: float = 0.2) -> dict:
    """Add a filled, bordered territory polygon; points outside the map are clamped."""
    pts = convert.to_points(points)
    col = convert.to_color(color)
    s = session()
    with s.mutation("add_territory") as doc:
        id_ = place.add_territory(doc, pts, col, opacity, style, width, smoothing)
    return _result([id_], f"Added territory with {len(pts)} points.")


# ----------------------------------------------------------------- editing
@tool_errors
def update_object(id: str, fields: dict) -> dict:
    """Update fields of one object (id like "label:3", see list_objects). Vectors
    (position, offset, scale) are [x, y]; colours are [r, g, b(, a)] floats 0-1 or "#rrggbb";
    points (path/territory) are [[x, y], ...]. Unknown keys are rejected with the valid list."""
    s = session()
    doc = s.doc()
    doc.get(id)  # validates the id
    kind = id.partition(":")[0]
    conv = convert.convert_fields(kind, fields)
    with s.mutation("update_object") as doc:
        doc.update(id, **conv)
    return _result([id], f"Updated {id}: {', '.join(conv) or 'nothing'}.")


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
    """Turn the decorative map frame on/off; texture (e.g. "celtic_dotted") and size optional."""
    s = session()
    with s.mutation("set_frame") as doc:
        f = doc.root["frame"]
        f["enabled"] = bool(enabled)
        if texture is not None:
            f["texture"] = texture
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
    target = Path(out_path).expanduser()
    if target.suffix == "":
        target = target.with_name(target.name + ".wonderdraft_map")
    saved = res.doc.save(target)
    s.set_current(res.doc, dirty=False)
    c = res.counts
    msg = (f"Built {spec.name!r} ({spec.width}x{spec.height}, theme {spec.theme!r}) and saved it to "
           f"{saved}: {c['settlements']} settlements, {c['realms']} realms, {c['roads']} roads, "
           f"{c['rivers']} rivers, {c['lakes']} lakes, {c['mountain_symbols']} mountain symbols, "
           f"{c['forest_symbols']} forest symbols, {c['labels']} labels. "
           f"It is the open map: call render_preview to see it, then refine with the edit tools.")
    if res.warnings:
        msg += " Warnings: " + "; ".join(res.warnings) + "."
    msg += " Returned ids cover labels, paths, territories and settlement symbols; "            "use list_objects for mountain and forest symbols. " + ID_NOTE
    return {"ids": res.ids, "summary": msg, "counts": c, "warnings": res.warnings,
            "path": str(saved)}


def region_spec_schema() -> dict:
    """JSON Schema of the RegionSpec that build_region takes (field meanings included)."""
    return region.RegionSpec.model_json_schema()


# ----------------------------------------------------------------- output
@tool_errors
def render_preview(scale: float = 0.5, bbox: list[int] | None = None,
                   show_ids: bool = False) -> list:
    """Render the map to a PNG you can see (also saved to disk; the path is in the text).
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
    text = f"Preview {img.width}x{img.height} (scale {scale}). Saved: {path}"
    return [Image(data=data, format="png"), text]


@tool_errors
def undo() -> dict:
    """Undo the last change (up to 20 steps; cleared when a map is created or opened)."""
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
    update_object, move_objects, remove_objects, apply_theme, set_frame,
    build_region, region_spec_schema,
    render_preview, undo,
]
for _fn in TOOLS:
    mcp.tool()(_fn)


def main() -> None:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    mcp.run()


if __name__ == "__main__":
    main()
