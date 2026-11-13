"""RegionSpec and the one-shot :func:`build_region`.

Claude fills a :class:`RegionSpec` from a DM's campaign notes and builds a full first-draft
map in one call; the edit tools then refine it. Coordinates are map pixels, origin top-left.

Build order (the Wonderdraft Quick Guide's): land -> mountains -> rivers and lakes -> ground
-> forests -> settlements -> roads -> realms -> labels. Every op gets its own seed derived
from ``spec.seed`` so a spec always builds the same map.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from wdmcp import convert
from wdmcp.assets.catalog import AssetNotFound, Catalog
from wdmcp.assets.themes import apply_theme
from wdmcp.model import raster_spec as rs
from wdmcp.model.document import MapDocument
from wdmcp.ops import place, terrain

SettlementKind = Literal["city", "town", "village", "castle", "fort", "ruin", "port",
                         "temple", "mine"]

# scale -> role -> built-in set key (all exist in the Wonderdraft 1.1.8 pck).
DEFAULT_SETS: dict[str, dict[str, str]] = {
    "world": {
        "mountains": "res://sprites/mountains/penned_mountains_small",
        "hills": "res://sprites/mountains/penned_hills",
        "forest": "res://sprites/trees/inked_pine",
    },
    "region": {
        "mountains": "res://sprites/mountains/penned_mountains_large",
        "hills": "res://sprites/mountains/penned_hills",
        "forest": "res://sprites/trees/inked_oak",
    },
}
_ROLE_CATEGORY = {"mountains": "mountains", "hills": "mountains", "forest": "trees"}

WATER_SEARCH_PX = 60
SEED_STRIDE = 10_000

Point = Annotated[list[float], Field(min_length=2, max_length=2,
                                     description="[x, y] in map pixels, origin top-left.")]
Polygon = Annotated[list[Point], Field(min_length=3, description=(
    "Closed polygon [[x, y], ...] in map pixels (do not repeat the first point); >= 3 points."))]
Polyline = Annotated[list[Point], Field(min_length=2, description=(
    "Open line [[x, y], ...] in map pixels; >= 2 points, in order."))]
ColorSpec = Annotated[str | list[float], Field(description=(
    "Colour as '#rrggbb', or [r, g, b] / [r, g, b, a] floats 0-1."))]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _check_color(v):
    if v is not None:
        convert.to_color(v)  # raises ValueError -> ValidationError
    return v


class LandShape(_Model):
    """One piece of land. Give exactly one of ``polygon`` (painted with a natural coast) or
    ``generate`` (procedural terrain). ``generate`` replaces ALL terrain painted before it, so
    list it first; later polygons are added on top."""
    polygon: Polygon | None = Field(None, description="Outline of the landmass in map pixels.")
    generate: Literal["continent", "archipelago", "island"] | None = Field(
        None, description="Generate procedural land instead of a polygon (overwrites existing land).")
    coverage: float = Field(0.4, ge=0, le=1, description=(
        "With generate: fraction of the whole map that becomes land (0-1)."))
    roughness: float = Field(0.5, ge=0, le=2, description=(
        "Coastline raggedness: 0 = straight polygon edges, 1 = very ragged. Polygon only."))

    @model_validator(mode="after")
    def _one_of(self):
        if (self.polygon is None) == (self.generate is None):
            raise ValueError("give exactly one of 'polygon' or 'generate'")
        return self


class Area(_Model):
    """A lake: a polygon of freshwater (inside land)."""
    polygon: Polygon
    roughness: float = Field(0.3, ge=0, le=2, description="Shoreline raggedness, 0 = smooth.")


class River(_Model):
    """A river from source to mouth. Put the last point in the sea (or a lake) so it ends there."""
    points: Polyline = Field(description="Course from the source (first) to the mouth (last).")
    width: float = Field(3.0, gt=0, le=60, description="River width in px at its widest.")


class MountainRange(_Model):
    """Mountain (or hill) symbols in a ribbon along a line."""
    polyline: Polyline = Field(description="Spine of the range in map pixels.")
    width: float = Field(60.0, gt=0, description="Ribbon width in px around the spine.")
    density: float = Field(3.0, ge=0, description="Symbols per 100x100 px of ribbon area.")
    kind: Literal["mountains", "hills"] = Field(
        "mountains", description="Which default symbol set to use when set_key is not given.")
    set_key: str | None = Field(None, description=(
        "Symbol set key (list_asset_sets, category mountains). Default depends on scale and kind."))


class Forest(_Model):
    """Trees scattered inside a polygon (land only)."""
    polygon: Polygon
    density: float = Field(5.0, ge=0, description="Trees per 100x100 px of area.")
    set_key: str | None = Field(None, description=(
        "Tree set key (list_asset_sets, category trees). Default depends on scale."))


class GroundPatch(_Model):
    """A patch of theme ground colour (the painted terrain tint under the symbols)."""
    polygon: Polygon
    color_index: int = Field(0, ge=0, description=(
        "0-based index into the theme's ground colours (Adventure has several earth/grass tones)."))
    softness: float = Field(0.5, ge=0, le=1, description="Edge feathering 0-1.")


class Settlement(_Model):
    """A settlement symbol with its name label just below it. A position on water is moved to
    the nearest land within 60 px (reported in warnings)."""
    name: str = Field(min_length=1)
    kind: SettlementKind = Field(description="Picks the default symbol and label size (city uses the City label preset).")
    pos: Point
    set_key: str | None = Field(None, description="Override the default symbol set for this kind.")
    scale: float = Field(1.0, gt=0, le=5, description=(
        "Size multiplier on top of the kind's default size (1 = default)."))
    variant: int | None = Field(None, ge=0, description=(
        "Pick one sprite of the set by index (list_asset_sets shows the count); default is "
        "chosen per kind from a curated list using the seed."))


class Road(_Model):
    """A road or path through points."""
    points: Polyline
    style: str = Field("path_blended", description="Path texture name (e.g. path_blended, dirt_road).")
    width: float = Field(4.0, gt=0, description="Width in px.")
    color: ColorSpec | None = Field(None, description="Override colour; default is the style's own.")
    roughness: float = Field(0.33, ge=0, le=2, description="Wobble 0-1.")

    @field_validator("color")
    @classmethod
    def _color_ok(cls, v):
        return _check_color(v)


class Realm(_Model):
    """A nation or province: a filled, bordered territory plus its name as a Region-style label
    at the polygon's centroid."""
    name: str = Field(min_length=1)
    polygon: Polygon
    color: ColorSpec
    opacity: float = Field(0.25, ge=0, le=1, description="Fill opacity 0-1.")

    @field_validator("color")
    @classmethod
    def _color_ok(cls, v):
        return _check_color(v)


class FreeLabel(_Model):
    """A free-standing text label (seas, ranges, forests, regions)."""
    text: str = Field(min_length=1)
    pos: Point
    preset: str = Field("Region", description=(
        "Theme label preset: Town, City, Region or Water in the built-in themes (falls back to Region if missing)."))
    size: int | None = Field(None, gt=0, description="Font size override; default from the preset.")
    rotation: float = Field(0.0, ge=-360, le=360, description=(
        "Rotation in degrees, clockwise on screen (90 = reading top to bottom)."))
    curve: float = Field(0.0, description=(
        "Wonderdraft's label curve value, stored as-is: 0 = straight text; a non-zero value "
        "bends the text along an arc, the sign picking the direction (scale not verified; "
        "start small, e.g. 0.1; the preview draws labels straight)."))


class RegionSpec(_Model):
    """A whole map described as data. Fill what the notes describe, leave the rest empty."""
    name: str = Field(description="Name of the region/map (used in the summary only).")
    width: int = Field(1920, ge=64, le=8192, description="Map width in px.")
    height: int = Field(1080, ge=64, le=8192, description="Map height in px.")
    theme: str = Field("Adventure", description="Built-in theme name (list_themes).")
    seed: int = Field(0, description="Master seed; every generated element derives its own from it.")
    scale: Literal["world", "region"] = Field(
        "region", description="world: small symbols for a whole-world map; region: larger symbols.")
    land: list[LandShape] = Field(default_factory=list, description=(
        "Landmasses. A map with no land is all sea. 'generate' shapes are applied first and "
        "polygons after them, whatever the list order, so polygons are never wiped."))
    lakes: list[Area] = Field(default_factory=list)
    rivers: list[River] = Field(default_factory=list)
    mountains: list[MountainRange] = Field(default_factory=list)
    ground: list[GroundPatch] = Field(default_factory=list)
    forests: list[Forest] = Field(default_factory=list)
    settlements: list[Settlement] = Field(default_factory=list)
    roads: list[Road] = Field(default_factory=list)
    realms: list[Realm] = Field(default_factory=list)
    labels: list[FreeLabel] = Field(default_factory=list)
    frame: bool = Field(True, description="Draw the decorative map frame.")

    @model_validator(mode="after")
    def _in_bounds(self):
        for kind, items, attr in (("settlement", self.settlements, "pos"),
                                  ("label", self.labels, "pos")):
            for it in items:
                x, y = getattr(it, attr)
                if not (0 <= x < self.width and 0 <= y < self.height):
                    nm = getattr(it, "name", None) or getattr(it, "text", "")
                    raise ValueError(f"{kind} {nm!r} at ({x}, {y}) is outside the "
                                     f"{self.width}x{self.height} map")
        return self


@dataclass
class RegionResult:
    doc: MapDocument
    counts: dict[str, int]
    warnings: list[str] = field(default_factory=list)
    ids: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- helpers
def polygon_centroid(points) -> tuple[float, float]:
    """Area centroid of a polygon; the vertex mean when the area is (near) zero."""
    a = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    x, y = a[:, 0], a[:, 1]
    x1, y1 = np.roll(x, -1), np.roll(y, -1)
    cross = x * y1 - x1 * y
    area2 = float(cross.sum())
    if abs(area2) <= 1e-9 * (1.0 + float(np.ptp(x) * np.ptp(y))):
        return float(x.mean()), float(y.mean())
    cx = float(((x + x1) * cross).sum() / (3.0 * area2))
    cy = float(((y + y1) * cross).sum() / (3.0 * area2))
    return cx, cy


def _water_mask(doc: MapDocument, x0: int, y0: int, x1: int, y1: int) -> np.ndarray:
    m = doc.raster("mask")[y0:y1, x0:x1]
    return ~rs.is_land(m[..., 3]) | (m[..., rs.FRESHWATER_CHANNEL] >= 128)


def _nearest_land(doc: MapDocument, x: float, y: float, radius: int = WATER_SEARCH_PX):
    """Nearest dry-land pixel to (x, y) within ``radius`` px, else None."""
    x0, y0 = max(int(x) - radius, 0), max(int(y) - radius, 0)
    x1, y1 = min(int(x) + radius + 1, doc.width), min(int(y) + radius + 1, doc.height)
    dry = ~_water_mask(doc, x0, y0, x1, y1)
    ys, xs = np.nonzero(dry)
    if len(xs) == 0:
        return None
    d = np.hypot(xs + x0 - x, ys + y0 - y)
    i = int(np.argmin(d))
    return (float(xs[i] + x0), float(ys[i] + y0)) if d[i] <= radius else None


def _sea_along(doc: MapDocument, points, step: float = 2.0) -> np.ndarray:
    """Whether each sample (every ``step`` px) along the polyline lies on sea (not land)."""
    a = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    samples = [a[:1]]
    for p, q in zip(a[:-1], a[1:]):
        n = max(1, int(np.ceil(np.hypot(*(q - p)) / step)))
        u = (np.arange(1, n + 1) / n)[:, None]
        samples.append(p + u * (q - p))
    s = np.vstack(samples)
    xi = np.clip(s[:, 0].astype(int), 0, doc.width - 1)
    yi = np.clip(s[:, 1].astype(int), 0, doc.height - 1)
    return ~rs.is_land(doc.raster("mask")[yi, xi, 3])


def river_warnings(doc: MapDocument, points, width: float, written: int) -> list[str]:
    """Warnings for a river that drew much less than expected or runs mostly over sea.

    ``written`` is the number of pixels add_river changed; a river fully on land changes about
    ``length * width`` pixels. The stretch at the mouth that lies in the sea is expected (rivers
    should end there) and is not counted as "on water"."""
    a = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    length = float(np.hypot(*np.diff(a, axis=0).T).sum())
    out: list[str] = []
    sea = _sea_along(doc, a)
    tail = len(sea) - (int(np.argmax(~sea[::-1])) if (~sea).any() else 0)  # mouth run
    inland = sea[:tail]
    frac = float(inland.mean()) if len(inland) else 1.0
    src = tuple(round(v) for v in a[0])
    if frac > 0.25:
        out.append(f"river from {src} runs over sea for about {frac:.0%} of its course "
                   f"before its mouth; rivers only draw on land")
    expected = 0.5 * length * width
    if written < expected:
        out.append(f"river from {src} drew only {written} px (expected about "
                   f"{int(length * width)} for {length:.0f} px at width {width:g}); "
                   f"part of it is probably off land or over other water")
    return out


def _on_water(doc: MapDocument, x: float, y: float) -> bool:
    xi, yi = min(max(int(x), 0), doc.width - 1), min(max(int(y), 0), doc.height - 1)
    return bool(_water_mask(doc, xi, yi, xi + 1, yi + 1)[0, 0])


class _Builder:
    def __init__(self, spec: RegionSpec, cat: Catalog):
        self.spec, self.cat = spec, cat
        self.warnings: list[str] = []
        self._n = 0
        self.counts = {"land": 0, "lakes": 0, "rivers": 0, "mountain_symbols": 0, "ground": 0,
                       "forest_symbols": 0, "settlements": 0, "roads": 0, "realms": 0,
                       "labels": 0}
        doc = MapDocument.new(spec.width, spec.height)
        apply_theme(doc, cat.get_theme(spec.theme))
        doc.root["frame"]["enabled"] = bool(spec.frame)
        self.doc = doc
        self.settle_entries: list[dict] = []

    def seed(self) -> int:
        self._n += 1
        return self.spec.seed * SEED_STRIDE + self._n

    def set_for(self, explicit: str | None, role: str) -> str:
        if explicit:
            return explicit
        key = DEFAULT_SETS[self.spec.scale][role]
        try:
            self.cat.get_set(key)
            return key
        except AssetNotFound:
            pass
        cands = self.cat.sets(_ROLE_CATEGORY[role])
        cands = [s for s in cands if s.variants]
        cands.sort(key=lambda s: not s.key.startswith("res://sprites/"))
        if not cands:
            raise ValueError(f"no {_ROLE_CATEGORY[role]} symbol sets are available for {role}")
        self.warnings.append(f"default {role} set {key} is not in the catalogue; "
                             f"used {cands[0].key} instead")
        return cands[0].key

    # sections ---------------------------------------------------------
    def land(self):
        # generate_landmass overwrites the whole mask, so it must come before any polygon
        for ls in sorted(self.spec.land, key=lambda x: x.generate is None):
            if ls.generate:
                terrain.generate_landmass(self.doc, ls.coverage, self.seed(), ls.generate)
            else:
                terrain.paint_land(self.doc, [tuple(p) for p in ls.polygon], "add",
                                   ls.roughness, self.seed())
            self.counts["land"] += 1

    def mountains(self):
        for m in self.spec.mountains:
            key = self.set_for(m.set_key, m.kind)
            ids, _ = place.mountain_range(self.doc, self.cat, key, m.polyline, m.width,
                                          m.density, self.seed())
            self.counts["mountain_symbols"] += len(ids)
            if not ids:
                self.warnings.append(f"mountain range along {m.polyline[0]} placed no symbols "
                                     f"(is it over land?)")

    def water(self):
        for r in self.spec.rivers:
            pts = [tuple(p) for p in r.points]
            n = terrain.add_river(self.doc, pts, r.width, self.seed())
            self.warnings.extend(river_warnings(self.doc, pts, r.width, n))
            self.counts["rivers"] += 1
        for lk in self.spec.lakes:
            terrain.add_lake(self.doc, [tuple(p) for p in lk.polygon], lk.roughness, self.seed())
            self.counts["lakes"] += 1

    def ground(self):
        for g in self.spec.ground:
            terrain.paint_ground(self.doc, [tuple(p) for p in g.polygon], g.color_index, g.softness)
            self.counts["ground"] += 1

    def forests(self):
        for f in self.spec.forests:
            key = self.set_for(f.set_key, "forest")
            ids, _ = place.forest(self.doc, self.cat, key, f.polygon, f.density, self.seed())
            self.counts["forest_symbols"] += len(ids)
            if not ids:
                self.warnings.append(f"forest at {f.polygon[0]} placed no trees (is it over land?)")

    def settlements(self):
        for s in self.spec.settlements:
            x, y = s.pos
            if _on_water(self.doc, x, y):
                spot = _nearest_land(self.doc, x, y)
                if spot is None:
                    self.warnings.append(
                        f"settlement {s.name!r} at ({x:g}, {y:g}) is on water; no land within "
                        f"{WATER_SEARCH_PX} px, placed there anyway")
                else:
                    self.warnings.append(
                        f"settlement {s.name!r} was on water at ({x:g}, {y:g}); moved to "
                        f"({spot[0]:g}, {spot[1]:g})")
                    x, y = spot
            ids = place.add_settlement(self.doc, self.cat, s.name, (x, y), s.kind, s.set_key,
                                       True, self.seed(), s.scale, s.variant, self.warnings)
            # symbol ids shift on later inserts, so keep the entry itself and resolve at the end
            self.settle_entries.append(self.doc.get(ids[0]))
            self.counts["settlements"] += 1
            self.counts["labels"] += 1

    def roads(self):
        for r in self.spec.roads:
            color = convert.to_color(r.color) if r.color is not None else None
            place.add_path(self.doc, [tuple(p) for p in r.points], r.style, r.width, color,
                           r.roughness, self.seed())
            self.counts["roads"] += 1

    def realms(self):
        for r in self.spec.realms:
            place.add_territory(self.doc, [tuple(p) for p in r.polygon],
                                convert.to_color(r.color), r.opacity)
            self.counts["realms"] += 1
        for r in self.spec.realms:
            cx, cy = polygon_centroid(r.polygon)
            cx = min(max(cx, 0.0), self.doc.width - 1.0)
            cy = min(max(cy, 0.0), self.doc.height - 1.0)
            place.add_label(self.doc, r.name, (cx, cy), "Region")
            self.counts["labels"] += 1

    def labels(self):
        for lb in self.spec.labels:
            preset = lb.preset
            try:
                place.label_preset_used(self.doc, preset)
            except ValueError:
                used = place.label_preset_used(self.doc, "Region")
                self.warnings.append(f"label {lb.text!r}: preset {preset!r} not in the theme, "
                                     f"used {used!r}")
                preset = used
            place.add_label(self.doc, lb.text, lb.pos, preset, lb.size, lb.rotation, lb.curve)
            self.counts["labels"] += 1

    def final_ids(self) -> list[str]:
        """Ids valid now: labels, paths, territories and settlement symbols (resolved
        by entry identity, since symbol ids shift while inserting). Mountain/forest symbols are
        left out; use list_objects for them."""
        ids = [i for k in ("label", "path", "territory") for i, _ in self.doc.list(k)]
        index = {id(e): sid for sid, e in self.doc.list("symbol")}
        sym = sorted((index[id(e)] for e in self.settle_entries),
                     key=lambda i: int(i.split(":")[1]))
        return sym + ids


def build(spec: RegionSpec, cat: Catalog) -> RegionResult:
    """Build the map and report counts, warnings and ids."""
    b = _Builder(spec, cat)
    b.land()
    b.mountains()
    b.water()
    b.ground()
    b.forests()
    b.settlements()
    b.roads()
    b.realms()
    b.labels()
    return RegionResult(b.doc, b.counts, b.warnings, b.final_ids())


def build_region(spec: RegionSpec, cat: Catalog) -> MapDocument:
    """Build a whole map from a spec, in the Quick Guide's order."""
    return build(spec, cat).doc
