"""JSON-friendly argument conversion for the MCP tools (no MCP imports here)."""
from __future__ import annotations

import difflib
import math
import re
from typing import Any

from wdmcp.codec.types import Color, Vector2
from wdmcp.model import template
from wdmcp.model.points import format_points

_HEX = re.compile(r"^#?([0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")

# Entry keys that hold a Color; custom_colors / custom_color_mode are passed through as-is.
COLOR_KEYS = ("color", "outline_color", "glow_color", "sample")
VECTOR_KEYS = ("position", "offset", "scale")


def _finite(v: Any, what: str) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{what}: expected a number, got {v!r}") from None
    if not math.isfinite(f):
        raise ValueError(f"{what}: non-finite number {v!r}")
    return f


def to_color(v: Any) -> Color:
    """``"#rrggbb[aa]"``, ``[r, g, b]`` or ``[r, g, b, a]`` (floats 0-1) -> Color."""
    if isinstance(v, Color):
        return v
    if isinstance(v, str):
        m = _HEX.match(v.strip())
        if not m:
            raise ValueError(f"bad colour {v!r}; use '#rrggbb' or [r, g, b] floats 0-1")
        h = m.group(1)
        vals = [int(h[i:i + 2], 16) / 255.0 for i in range(0, len(h), 2)]
        return Color(*vals) if len(vals) == 4 else Color(*vals, 1.0)
    try:
        vals = [_finite(x, "colour") for x in v]
    except TypeError:
        raise ValueError(f"bad colour {v!r}; use '#rrggbb' or [r, g, b] floats 0-1") from None
    if len(vals) not in (3, 4):
        raise ValueError(f"colour needs 3 or 4 components, got {len(vals)}")
    return Color(*vals) if len(vals) == 4 else Color(*vals, 1.0)


def to_vec2(v: Any, what: str = "vector") -> Vector2:
    if isinstance(v, Vector2):
        return v
    try:
        x, y = v
    except (TypeError, ValueError):
        raise ValueError(f"{what}: expected [x, y], got {v!r}") from None
    return Vector2(_finite(x, what), _finite(y, what))


def to_points(v: Any, what: str = "points") -> list[tuple[float, float]]:
    try:
        return [(_finite(p[0], what), _finite(p[1], what)) for p in v]
    except (TypeError, IndexError):
        raise ValueError(f"{what}: expected [[x, y], ...], got {v!r}") from None


INT64 = (-(2 ** 63), 2 ** 63 - 1)
# Keys whose template default is None: sample may be a colour or null, the others only null.
NULLABLE_COLOR = ("sample",)
NULL_ONLY = ("custom_colors", "custom_color_mode")


def _template_entry(kind: str) -> dict:
    if kind == "symbol":
        return template.symbol_entry("t", (0, 0))
    if kind == "label":
        return template.label_entry("t", (0, 0))
    if kind == "path":
        return template.path_entry([])
    if kind == "territory":
        return template.territory_entry([])
    raise ValueError(f"unknown kind {kind!r}")


def valid_keys(kind: str) -> list[str]:
    keys = list(_template_entry(kind))
    if kind == "symbol":
        keys.append("variant")
    return keys


def _coerce(key: str, v: Any, default: Any) -> Any:
    """Coerce a JSON value to the type of the template default for ``key``."""
    if isinstance(default, bool):
        if not isinstance(v, bool):
            raise ValueError(f"{key}: expected true or false, got {v!r}")
        return v
    if isinstance(default, int):
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError(f"{key}: expected an integer, got {v!r}")
        if isinstance(v, float):
            if not math.isfinite(v) or not v.is_integer():
                raise ValueError(f"{key}: expected an integer, got {v!r}")
            v = int(v)
        if not INT64[0] <= v <= INT64[1]:
            raise ValueError(f"{key}: {v} is outside the 64-bit integer range")
        return int(v)
    if isinstance(default, float):
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError(f"{key}: expected a number, got {v!r}")
        return _finite(v, key)
    if isinstance(default, str):
        if not isinstance(v, str):
            raise ValueError(f"{key}: expected a string, got {v!r}")
        return v
    if isinstance(default, Vector2):
        return to_vec2(v, key)
    if isinstance(default, Color):
        if v is None:
            raise ValueError(f"{key}: a colour is required, got null")
        return to_color(v)
    raise ValueError(f"{key}: cannot be set")  # pragma: no cover


def _scale_factor(v: Any) -> tuple[float, float]:
    """``scale`` for symbols: a number or [x, y], multiplier(s) on the set's base scale."""
    if isinstance(v, bool):
        raise ValueError(f"scale: expected a number or [x, y], got {v!r}")
    if isinstance(v, (int, float)):
        f = _finite(v, "scale")
        return f, f
    p = to_vec2(v, "scale")
    return p.x, p.y


def _set_for(cat, tex: str):
    """The catalogue set ``tex`` names (a set key) or belongs to (a sprite), else None."""
    sets = getattr(cat, "_sets", None) or {}
    return sets.get(tex) or sets.get(tex.rsplit("/", 1)[0])


def _resolve_symbol_texture(entry: dict, texture: str | None, variant: Any, cat):
    """(new texture, its set or None) from ``texture`` and/or ``variant``.

    ``variant`` is an index into the sprites of a set: the set named by ``texture`` (a set key
    or one of its sprites) when both are given, else the symbol's current set."""
    from wdmcp.assets.catalog import AssetNotFound
    sets = getattr(cat, "_sets", None) or {}
    suggest = getattr(cat, "_suggest", lambda k: [])
    if variant is not None:
        if isinstance(variant, bool) or not isinstance(variant, int):
            raise ValueError(f"variant: expected an integer index, got {variant!r}")
        base = texture if texture is not None else entry["texture"]
        s = _set_for(cat, base)
        if s is None:
            key = base if base in sets else base.rsplit("/", 1)[0]
            raise AssetNotFound(key, suggest(key))
        if not 0 <= variant < len(s.variants):
            raise ValueError(f"variant {variant} out of range for {s.key!r} "
                             f"(0..{len(s.variants) - 1})")
        return s.variants[variant], s
    if texture in sets:
        raise ValueError(f"texture {texture!r} is a symbol set; pass one of its sprites, or "
                         f"give variant to pick one by index")
    s = _set_for(cat, texture)
    if s is not None:
        if texture not in s.variants:
            raise AssetNotFound(texture, difflib.get_close_matches(texture, s.variants, n=5))
        return texture, s
    if getattr(cat, "_pck", None) is not None and texture.startswith("res://"):
        raise AssetNotFound(texture, suggest(texture.rsplit("/", 1)[0]))
    return texture, None  # set unknown to this catalogue (e.g. no pck): stored as given


def convert_fields(kind: str, fields: dict, entry: dict | None = None, cat=None) -> dict:
    """Convert a JSON ``fields`` dict for update_object into stored values.

    Every value is coerced to the type of Wonderdraft's default for that key (str, int within
    int64, float, bool, [x, y], colour); wrong types are rejected, as are unknown keys (the
    message lists the valid ones). Special keys:

    * ``rotation``: degrees clockwise; stored as radians for symbols, degrees for labels.
    * ``scale`` (symbols): a number or [x, y], a multiplier on the set's base scale, the same
      meaning as add_symbol's ``scale`` (stored scale = base x multiplier; base is 1 for
      built-in sprites and 0.25 for user packs).
    * ``texture`` / ``variant`` (symbols): ``variant`` picks a sprite by index from the set of
      ``texture`` (a set key or sprite, if given) or else of the current texture, resolved via
      the catalogue. A different set also brings its offset, radius and type unless those are
      given; the size multiplier is kept. Validated when the catalogue knows the set.
    * ``style`` (paths/territories): a path or border texture name, validated like add_path.
    * ``sample`` may be a colour or null; ``custom_colors`` / ``custom_color_mode`` only null.
    """
    from wdmcp.ops import place  # local import: place imports the catalogue
    valid = valid_keys(kind)
    bad = [k for k in fields if k not in valid]
    if bad:
        raise ValueError(f"unknown field(s) for {kind}: {', '.join(map(repr, bad))}. "
                         f"Valid keys: {', '.join(valid)}")
    tmpl = _template_entry(kind)
    out: dict[str, Any] = {}
    for k, v in fields.items():
        if kind == "symbol" and k in ("variant", "scale", "texture"):
            continue  # interdependent, handled below
        if k == "points":
            out[k] = format_points(to_points(v))
        elif k == "rotation":
            if isinstance(v, bool):
                raise ValueError(f"rotation: expected a number of degrees, got {v!r}")
            deg = _finite(v, "rotation")
            out[k] = math.radians(deg) if kind == "symbol" else deg
        elif k in NULLABLE_COLOR:
            out[k] = None if v is None else to_color(v)
        elif k in NULL_ONLY:
            if v is not None:
                raise ValueError(f"{k}: only null (reset to default) is supported")
            out[k] = None
        elif k == "style":
            if not isinstance(v, str):
                raise ValueError(f"style: expected a string, got {v!r}")
            out[k] = place.resolve_style(cat, "paths" if kind == "path" else "borders", v)
        else:
            out[k] = _coerce(k, v, tmpl[k])
    if kind != "symbol":
        return out
    old_tex = entry["texture"] if entry is not None else ""
    new_tex = old_tex
    tex = fields.get("texture")
    if tex is not None and not isinstance(tex, str):
        raise ValueError(f"texture: expected a string, got {tex!r}")
    if "texture" in fields and tex is None:
        raise ValueError("texture: a string is required, got null")
    if tex is not None or fields.get("variant") is not None:
        if fields.get("variant") is None and entry is None:
            new_tex, new_set = tex, None
        else:
            new_tex, new_set = _resolve_symbol_texture(entry or {"texture": tex}, tex,
                                                       fields.get("variant"), cat)
        out["texture"] = new_tex
        old_set = _set_for(cat, old_tex) if old_tex else None
        if new_set is not None and new_set is not old_set:
            for key, val in (("offset", Vector2(*new_set.offset)),
                             ("radius", float(new_set.radius)),
                             ("type", place._TYPE.get(new_set.category, "symbol"))):
                out.setdefault(key, val)
    new_base = place._base_scale(new_tex)
    if "scale" in fields:
        fx, fy = _scale_factor(fields["scale"])
        out["scale"] = Vector2(new_base * fx, new_base * fy)
    elif entry is not None and new_tex != old_tex:
        old_base = place._base_scale(old_tex)
        if new_base != old_base:  # keep the size multiplier across built-in <-> user packs
            sc = entry["scale"]
            out["scale"] = Vector2(sc.x / old_base * new_base, sc.y / old_base * new_base)
    return out
