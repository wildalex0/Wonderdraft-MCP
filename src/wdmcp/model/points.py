"""Godot ``PoolVector2Array``-as-string helpers used by paths and territories.

Wonderdraft stores path/territory points as the text
``"[ Vector2( 526, 297 ), Vector2( 539, 340 ) ]"``.
"""
import re

_VEC = re.compile(r"Vector2\(\s*([^,()\s]+)\s*,\s*([^,()\s]+)\s*\)")


def _num(v: float) -> str:
    v = float(v)
    if v.is_integer():
        return str(int(v))
    s = repr(v)
    if "e" not in s and "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def format_points(pts: list[tuple[float, float]]) -> str:
    if not pts:
        return "[  ]"
    return "[ " + ", ".join(f"Vector2( {_num(x)}, {_num(y)} )" for x, y in pts) + " ]"


def parse_points(s: str) -> list[tuple[float, float]]:
    return [(float(a), float(b)) for a, b in _VEC.findall(s)]
