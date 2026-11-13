"""JSON-friendly argument conversion for the MCP tools (no MCP imports here)."""
from __future__ import annotations

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


def valid_keys(kind: str) -> list[str]:
    if kind == "symbol":
        e = template.symbol_entry("t", (0, 0))
    elif kind == "label":
        e = template.label_entry("t", (0, 0))
    elif kind == "path":
        e = template.path_entry([])
    elif kind == "territory":
        e = template.territory_entry([])
    else:
        raise ValueError(f"unknown kind {kind!r}")
    return list(e)


def convert_fields(kind: str, fields: dict) -> dict:
    """Convert a JSON ``fields`` dict into codec types, rejecting unknown keys."""
    valid = valid_keys(kind)
    bad = [k for k in fields if k not in valid]
    if bad:
        raise ValueError(f"unknown field(s) for {kind}: {', '.join(map(repr, bad))}. "
                         f"Valid keys: {', '.join(valid)}")
    out: dict[str, Any] = {}
    for k, v in fields.items():
        if k in VECTOR_KEYS:
            out[k] = to_vec2(v, k)
        elif k in COLOR_KEYS:
            out[k] = None if v is None else to_color(v)
        elif k == "points":
            out[k] = format_points(to_points(v))
        elif k == "rotation":
            deg = _finite(v, "rotation")
            out[k] = math.radians(deg) if kind == "symbol" else deg
        else:
            out[k] = v
    return out
