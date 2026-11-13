"""Fast, readable (not pixel-perfect) PNG preview of a Wonderdraft map.

Everything is composited at output resolution: rasters are box-downscaled first.
"""
from __future__ import annotations

import math
import weakref
from collections import OrderedDict

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from wdmcp.assets.catalog import AssetNotFound
from wdmcp.codec.types import Color
from wdmcp.model import raster_spec
from wdmcp.model.points import parse_points

_DEF_WATER = (0.57, 0.80, 0.72, 1.0)
_DEF_GROUND = (0.90, 0.82, 0.67, 1.0)
_MAGENTA = (255, 0, 255)
_BOX = Image.Resampling.BOX

_SPRITE_CACHE_BYTES = 256 * 1024 * 1024
_SPRITE_MAX_SIDE = 2048
_RAW_CACHE_MAX = 128
_font_cache: dict = {}
_MISS = object()


class _Cache:
    """Decoded and resized sprites for ONE catalog; dies with it (incl. missing-texture memory)."""

    def __init__(self):
        self.raw: OrderedDict = OrderedDict()    # texture -> RGBA image | None (missing)
        self.sized: OrderedDict = OrderedDict()  # (texture, w, h) -> RGBA image
        self.bytes = 0

    def get(self, cache, key):
        v = cache.get(key, _MISS)
        if v is not _MISS:
            cache.move_to_end(key)
        return v

    def put_raw(self, key, im):
        self.raw[key] = im
        if len(self.raw) > _RAW_CACHE_MAX:
            self.raw.popitem(last=False)

    def put_sized(self, key, im):
        self.sized[key] = im
        self.bytes += im.width * im.height * 4
        while self.bytes > _SPRITE_CACHE_BYTES and len(self.sized) > 1:
            _k, old = self.sized.popitem(last=False)
            self.bytes -= old.width * old.height * 4


_catalog_caches: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()
_none_cache = _Cache()


def _cache_for(cat) -> _Cache:
    if cat is None:
        return _none_cache
    try:
        c = _catalog_caches.get(cat)
        if c is None:
            c = _catalog_caches[cat] = _Cache()
        return c
    except TypeError:  # not weak-referenceable / hashable: no caching
        return _Cache()


def _rgba(c, default):
    if isinstance(c, Color):
        return (c.r, c.g, c.b, c.a)
    if (isinstance(c, (tuple, list)) and len(c) in (3, 4)
            and all(isinstance(v, (int, float)) for v in c)):
        return (*c[:3], c[3] if len(c) == 4 else 1.0)
    return default


def _theme_color(theme, key, default):
    return _rgba(theme.get(key), default)


def _theme_first(theme, key, default):
    v = theme.get(key)
    if isinstance(v, (list, tuple)) and v:
        return _rgba(v[0], default)
    return default


def _theme_list(theme, key, default):
    v = theme.get(key)
    out = [_rgba(c, None) for c in v] if isinstance(v, (list, tuple)) else []
    out = [c for c in out if c is not None]
    return out or [default]


def _font(size: float):
    size = max(6, int(round(size)))
    f = _font_cache.get(size)
    if f is None:
        try:
            f = ImageFont.load_default(size=size)
        except (TypeError, OSError):  # Pillow < 10.1
            f = ImageFont.load_default()
        _font_cache[size] = f
    return f


def _fnum(v, default=0.0):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else default


def _vec(v, default=(0.0, 0.0)):
    return (_fnum(getattr(v, "x", None), default[0]), _fnum(getattr(v, "y", None), default[1]))


# -- rasters ------------------------------------------------------------------
def _channel(arr2d: np.ndarray, bbox, out_size) -> np.ndarray:
    """Crop to bbox (zero padded outside the map), box-resize to out_size, float32."""
    x0, y0, x1, y1 = bbox
    h, w = arr2d.shape
    sub = np.zeros((y1 - y0, x1 - x0), dtype=np.float32)
    cx0, cy0, cx1, cy1 = max(x0, 0), max(y0, 0), min(x1, w), min(y1, h)
    if cx1 > cx0 and cy1 > cy0:
        sub[cy0 - y0:cy1 - y0, cx0 - x0:cx1 - x0] = arr2d[cy0:cy1, cx0:cx1]
    if (x1 - x0, y1 - y0) == out_size:
        return sub
    return np.asarray(Image.fromarray(sub, "F").resize(out_size, _BOX), dtype=np.float32)


def _blend(base: np.ndarray, rgb, alpha: np.ndarray):
    """In place: base = base*(1-a) + rgb*a. ``rgb`` is a 0-255 triple or (h,w,3) array."""
    a = alpha.astype(np.float32)[..., None]
    base *= (1.0 - a)
    base += np.asarray(rgb, np.float32) * a


def _edge(b: np.ndarray) -> np.ndarray:
    """Pixels of boolean ``b`` with at least one 4-neighbour outside it."""
    p = np.pad(b, 1, mode="edge")
    inner = p[:-2, 1:-1] & p[2:, 1:-1] & p[1:-1, :-2] & p[1:-1, 2:]
    return b & ~inner


def _ground_rgb(height01: np.ndarray, colors) -> np.ndarray:
    pal = np.asarray(colors, np.float32)
    if len(pal) == 1:
        return np.broadcast_to(pal[0], height01.shape + (3,)).copy()
    t = np.clip(height01, 0, 1) * (len(pal) - 1)
    i = np.minimum(t.astype(int), len(pal) - 2)
    f = (t - i)[..., None]
    return pal[i] * (1 - f) + pal[i + 1] * f


def _paint_layer(raster: np.ndarray, bbox, out_size):
    """Downscale an RGBA paint raster with premultiplication -> (rgb 0-255, alpha 0-1) or None."""
    a = raster[..., 3]
    if not a.any():
        return None
    af = _channel(a, bbox, out_size) / 255.0
    pm = np.stack([_channel(raster[..., c].astype(np.float32) * a / 255.0, bbox, out_size)
                   for c in range(3)], -1)
    rgb = np.where(af[..., None] > 1e-4, pm / np.maximum(af[..., None], 1e-4), 0)
    return np.clip(rgb, 0, 255), af


_OK_DIST = 70.0       # Euclidean RGB distance at/above which a theme is left alone
_MIN_MEAN_DIFF = 60.0  # target mean |dRGB| for themes that have to be adjusted


def _dist(a, b) -> float:
    return float(np.linalg.norm(np.asarray(a, np.float32) - np.asarray(b, np.float32)))


def _mean_diff(a, b) -> float:
    return float(np.mean(np.abs(np.asarray(a, np.float32) - np.asarray(b, np.float32))))


def _ensure_contrast(water, gcols, coast):
    """Return (water_rgb, land_palette_rgb), both 0-255, so coastal land never blends into water.

    Themes that already contrast (hue or brightness, e.g. teal water vs tan land) are returned
    unchanged. Otherwise the water is darkened and pushed towards blue in fixed steps until the
    mean |dRGB| is >= 60; if that is not enough the land is lightened. The check uses the
    open water and the shore-lit water (after the coastal halo) against the two lowest ground colours.
    """
    pal = np.array([c[:3] for c in gcols], np.float32) * 255
    water = np.array(water[:3], np.float32) * 255
    halo = 0.6 * coast[3]
    cw = np.array(coast[:3], np.float32) * 255
    low = slice(0, 2)

    def shore(w):
        return w * (1 - halo) + cw * halo

    def ok(w, p, strict):
        return all(_dist(x, c) >= _OK_DIST and (not strict or _mean_diff(x, c) >= _MIN_MEAN_DIFF)
                   for x in (w, shore(w)) for c in p[low])

    if ok(water, pal, False):
        return water, pal
    for i in range(1, 15):
        k = 1.0 - 0.05 * i
        w = water * k
        w[0] *= k  # extra red drop pushes towards blue
        w[2] = min(255.0, water[2] * (1.0 + 0.02 * i))
        if ok(w, pal, True):
            return w, pal
    w = water * 0.3
    w[2] = min(255.0, water[2] * 0.6)
    for i in range(1, 11):
        p = pal + (255.0 - pal) * (0.05 * i)
        if ok(w, p, True):
            return w, p
    return w, pal + (255.0 - pal) * 0.5


def _base_layer(doc, theme, bbox, out_size) -> np.ndarray:
    ow, oh = out_size
    mask = doc.raster("mask")
    coast = _theme_color(theme, "coastline_color", (0.6, 0.77, 0.72, 0.7))
    water, gpal = _ensure_contrast(_theme_first(theme, "water_colors", _DEF_WATER),
                                   _theme_list(theme, "ground_colors", _DEF_GROUND), coast)
    a_full = mask[..., 3]
    is_land_full = a_full >= raster_spec.SEA_LEVEL_ALPHA
    land_w = _channel(is_land_full, bbox, out_size)  # exact coverage, so the coast stays crisp
    land_alpha = _channel(np.where(is_land_full, a_full, 0), bbox, out_size)
    water_depth = _channel(np.where(is_land_full, 0, a_full), bbox, out_size)
    out = np.empty((oh, ow, 3), np.float32)
    out[:] = water
    # lighter shallow water near the shore (mask alpha rises towards land)
    halo = water_depth / 127.0 * coast[3] * 0.6
    _blend(out, np.array(coast[:3], np.float32) * 255, halo)
    tint = _paint_layer(doc.raster("water_tint"), bbox, out_size)
    if tint is not None:
        _blend(out, tint[0], tint[1] * (1 - land_w))
    h01 = (land_alpha / np.maximum(land_w, 1e-3) - 128.0) / 127.0
    land = _ground_rgb(np.clip(h01, 0, 1), gpal)
    ground = _paint_layer(doc.raster("ground"), bbox, out_size)
    if ground is not None:
        _blend(land, ground[0], ground[1])
    _blend(out, land, land_w)
    is_land = land_w >= 0.5
    outline = _theme_color(theme, "landmass_outline_color", (0.24, 0.22, 0.16, 1.0))
    _blend(out, np.array(outline[:3], np.float32) * 255, _edge(is_land) * 0.9)
    fw = _channel(mask[..., raster_spec.FRESHWATER_CHANNEL], bbox, out_size) / 255.0
    fw = fw * is_land
    if fw.max() > 0:
        fc = _theme_color(theme, "freshwater_color", (0.55, 0.75, 0.77, 0.4))
        fo = _theme_color(theme, "freshwater_outline_color", (0.04, 0.03, 0.0, 1.0))
        _blend(out, np.array(fc[:3], np.float32) * 255, fw * max(fc[3], 0.85))
        _blend(out, np.array(fo[:3], np.float32) * 255, _edge(fw > 0.5) * 0.8)
    return out


# -- vectors ------------------------------------------------------------------
def _rgb255(c):
    return tuple(int(round(max(0.0, min(1.0, v)) * 255)) for v in c[:3])


def _dashed(draw, pts, fill, width, dash, gap):
    """Draw polyline ``pts`` as dashes by arc length."""
    on, left = True, dash
    for (ax, ay), (bx, by) in zip(pts, pts[1:]):
        seg = math.hypot(bx - ax, by - ay)
        pos = 0.0
        while pos < seg:
            step = min(left, seg - pos)
            if on:
                s0, s1 = pos / seg, (pos + step) / seg
                draw.line([(ax + (bx - ax) * s0, ay + (by - ay) * s0),
                           (ax + (bx - ax) * s1, ay + (by - ay) * s1)], fill=fill, width=width)
            pos += step
            left -= step
            if left <= 1e-9:
                on = not on
                left = dash if on else gap


def _to_out(pts, x0, y0, s):
    return [((x - x0) * s, (y - y0) * s) for x, y in pts]


def _points(entry):
    try:
        return parse_points(entry.get("points", ""))
    except (TypeError, ValueError):
        return []


def _draw_territories(img: Image.Image, doc, bbox, s, tags):
    x0, y0 = bbox[:2]
    for tid, t in doc.list("territory"):
        pts = _points(t)
        if len(pts) < 3:
            continue
        o = _to_out(pts, x0, y0, s)
        col = _rgba(t.get("color"), (0.5, 0.5, 0.5, 1.0))
        op = max(0.0, min(1.0, _fnum(t.get("opacity"), 0.25)))
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        d.polygon(o, fill=(*_rgb255(col), int(round(255 * op * col[3]))))
        w = max(1, int(round(_fnum(t.get("width"), 10.0) * s * 0.5)))
        closed = o + [o[0]]
        line = (*_rgb255(col), 220)
        if str(t.get("style", "")).endswith("dash"):
            _dashed(d, closed, line, w, 6 * w, 4 * w)
        else:
            d.line(closed, fill=line, width=w, joint="curve")
        img.alpha_composite(layer)
        tags.append((tid, o[0]))


def _draw_paths(img: Image.Image, doc, bbox, s, tags):
    x0, y0 = bbox[:2]
    d = ImageDraw.Draw(img)
    for pid, p in doc.list("path"):
        pts = _points(p)
        if len(pts) < 2:
            continue
        o = _to_out(pts, x0, y0, s)
        fill = _rgb255(_rgba(p.get("color"), (0.3, 0.19, 0.1, 1.0)))
        w = max(1, int(round(_fnum(p.get("width"), 4.0) * s)))
        if str(p.get("style", "")).endswith("dash"):
            _dashed(d, o, fill, w, 5 * w, 3 * w)
        else:
            d.line(o, fill=fill, width=w, joint="curve")
        tags.append((pid, o[0]))


# -- symbols ------------------------------------------------------------------
def _raw_sprite(cat, texture):
    cache = _cache_for(cat)
    v = cache.get(cache.raw, texture)
    if v is not _MISS:
        return v
    try:
        im = cat.image(texture).convert("RGBA")
    except (AssetNotFound, OSError):
        im = None
    cache.put_raw(texture, im)
    return im


def _sized_sprite(cat, texture, w, h):
    longest = max(w, h)
    if longest > _SPRITE_MAX_SIDE:
        k = _SPRITE_MAX_SIDE / longest
        w, h = max(1, int(w * k)), max(1, int(h * k))
    cache = _cache_for(cat)
    key = (texture, w, h)
    v = cache.get(cache.sized, key)
    if v is not _MISS:
        return v
    raw = _raw_sprite(cat, texture)
    # premultiplied resize avoids dark fringes round transparent edges
    arr = np.asarray(raw, np.float32)
    a = arr[..., 3:4] / 255.0
    pm = Image.fromarray(np.concatenate([arr[..., :3] * a, arr[..., 3:4]], -1).astype(np.uint8), "RGBA")
    down = w < raw.width and h < raw.height
    pm = pm.resize((w, h), _BOX if down else Image.Resampling.BILINEAR)
    p = np.asarray(pm, np.float32)
    al = p[..., 3:4]
    rgb = np.where(al > 0, p[..., :3] * 255.0 / np.maximum(al, 1), 0)
    out = Image.fromarray(np.concatenate([np.clip(rgb, 0, 255), al], -1).astype(np.uint8), "RGBA")
    cache.put_sized(key, out)
    return out


def _is_sample_color(cat, texture, memo) -> bool:
    d = texture.rsplit("/", 1)[0]
    if d not in memo:
        try:
            memo[d] = cat.get_set(d).draw_mode == "sample_color"
        except AssetNotFound:
            memo[d] = False
    return memo[d]


def _draw_symbols(img: Image.Image, doc, cat, bbox, s, tags):
    x0, y0 = bbox[:2]
    items = sorted(((_fnum(e.get("z_index")), _vec(e.get("position"))[1], sid, e)
                    for sid, e in doc.list("symbol")), key=lambda t: (t[0], t[1]))
    memo: dict = {}
    d = ImageDraw.Draw(img)
    for _z, _y, sid, e in items:
        px_, py_ = _vec(e.get("position"))
        ox, oy = _vec(e.get("offset"))
        sx, sy = _vec(e.get("scale"), (1.0, 1.0))
        cx, cy = (px_ + ox - x0) * s, (py_ + oy - y0) * s
        tex = str(e.get("texture", ""))
        raw = _raw_sprite(cat, tex) if cat is not None else None
        if raw is None:
            side = max(5, int(round(30 * max(abs(sx), 0.1) * s)))
            if -side <= cx <= img.width + side and -side <= cy <= img.height + side:
                d.rectangle([cx - side / 2, cy - side / 2, cx + side / 2, cy + side / 2],
                            fill=_MAGENTA, outline=(0, 0, 0))
                tags.append((sid, (cx, cy)))
            continue
        w = max(1, int(round(raw.width * abs(sx) * s)))
        h = max(1, int(round(raw.height * abs(sy) * s)))
        reach = w + h
        if cx < -reach or cy < -reach or cx > img.width + reach or cy > img.height + reach:
            continue
        spr = _sized_sprite(cat, tex, w, h)
        if (sx < 0) != bool(e.get("mirror")):
            spr = spr.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        if sy < 0:
            spr = spr.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        samp = e.get("sample")
        if isinstance(samp, Color) and _is_sample_color(cat, tex, memo):
            arr = np.asarray(spr, np.float32).copy()
            arr[..., :3] *= np.array([samp.r, samp.g, samp.b], np.float32)
            spr = Image.fromarray(arr.astype(np.uint8), "RGBA")
        rot = _fnum(e.get("rotation"))
        if rot:
            spr = spr.rotate(-math.degrees(rot), Image.Resampling.BICUBIC, expand=True)
        img.paste(spr.convert("RGB"), (int(round(cx - spr.width / 2)), int(round(cy - spr.height / 2))), spr)
        tags.append((sid, (cx, cy)))


# -- labels -------------------------------------------------------------------
def _draw_labels(img: Image.Image, doc, bbox, s, tags):
    x0, y0 = bbox[:2]
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    for lid, e in doc.list("label"):
        text = str(e.get("text", ""))
        px_, py_ = _vec(e.get("position"))
        cx, cy = (px_ - x0) * s, (py_ - y0) * s
        if not text:
            continue
        size = _fnum(e.get("size"), 24.0) * s
        sw = min(4, int(round(_fnum(e.get("outline_size")) * s)))
        oc = _rgba(e.get("outline_color"), (1, 1, 1, 1))
        kw = dict(font=_font(size), fill=(*_rgb255(_rgba(e.get("color"), (0, 0, 0, 1))), 255),
                  stroke_width=sw, stroke_fill=(*_rgb255(oc), int(round(255 * oc[3]))))
        align = {0: "left", 2: "right"}.get(int(_fnum(e.get("align"), 1)), "center")
        rot = _fnum(e.get("rotation"))
        if not rot:
            ImageDraw.Draw(layer).multiline_text((cx, cy), text, anchor={"left": "lm", "right": "rm"}.get(align, "mm"),
                                                 align=align, **kw)
        else:
            lines = text.count("\n") + 1
            tmp = Image.new("RGBA", (int(len(text) * size) + 20, int(size * 2 * lines) + 20), (0, 0, 0, 0))
            ImageDraw.Draw(tmp).multiline_text((tmp.width / 2, tmp.height / 2), text, anchor="mm",
                                               align=align, **kw)
            # label rotation is stored in degrees (symbols use radians)
            tmp = tmp.rotate(-rot, Image.Resampling.BICUBIC, expand=True)
            layer.paste(tmp, (int(round(cx - tmp.width / 2)), int(round(cy - tmp.height / 2))), tmp)
        tags.append((lid, (cx, cy)))
    img.alpha_composite(layer)


def _draw_tags(img: Image.Image, tags):
    d = ImageDraw.Draw(img)
    font = _font(11)
    for tid, (x, y) in tags:
        if not (-20 <= x <= img.width + 20 and -20 <= y <= img.height + 20):
            continue
        l, t, r, b = d.textbbox((x + 3, y - 14), tid, font=font)
        d.rectangle([l - 1, t - 1, r + 1, b + 1], fill=(255, 235, 90), outline=(0, 0, 0))
        d.text((x + 3, y - 14), tid, font=font, fill=(0, 0, 0))


# -- entry point --------------------------------------------------------------
def render_preview(doc, cat, scale: float = 0.5, bbox: tuple[int, int, int, int] | None = None,
                   show_ids: bool = False) -> Image.Image:
    """Render ``doc`` to an RGB image of round((x1-x0)*scale) x round((y1-y0)*scale).

    ``bbox`` is (x0, y0, x1, y1) in map pixels; ``cat`` may be None (symbols become placeholders).
    """
    if scale <= 0:
        raise ValueError("scale must be positive")
    if bbox is None:
        bbox = (0, 0, doc.width, doc.height)
    x0, y0, x1, y1 = (int(round(v)) for v in bbox)
    if x1 <= x0 or y1 <= y0:
        raise ValueError(f"empty bbox {bbox}")
    bbox = (x0, y0, x1, y1)
    out_size = (max(1, round((x1 - x0) * scale)), max(1, round((y1 - y0) * scale)))
    theme = doc.root.get("theme")
    if not isinstance(theme, dict):
        theme = {}
    base = _base_layer(doc, theme, bbox, out_size)
    img = Image.fromarray(np.clip(base + 0.5, 0, 255).astype(np.uint8), "RGB").convert("RGBA")
    tags: list = []
    _draw_territories(img, doc, bbox, scale, tags)
    rgb = img.convert("RGB")
    _draw_paths(rgb, doc, bbox, scale, tags)
    _draw_symbols(rgb, doc, cat, bbox, scale, tags)
    img = rgb.convert("RGBA")
    _draw_labels(img, doc, bbox, scale, tags)
    img = img.convert("RGB")
    if show_ids:
        _draw_tags(img, tags)
    return img
