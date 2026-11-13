"""Apply a Wonderdraft theme JSON onto a map's ``root["theme"]``."""
from __future__ import annotations

import copy
from typing import Any

from wdmcp.codec.types import Color, PoolColor, PoolReal
from wdmcp.model.document import MapDocument


def _to_color(v: Any) -> Color | None:
    if isinstance(v, Color):
        return v
    try:
        if isinstance(v, str):
            parts = [float(x) for x in v.split(",")]
        elif isinstance(v, (list, tuple)):
            parts = [float(x) for x in v]
        else:
            return None
    except ValueError:
        return None
    if len(parts) == 3:
        parts.append(1.0)
    return Color(*parts) if len(parts) == 4 else None


def _guess(v: Any, key: str) -> Any:
    """No template available: colour-named string keys become Color, rest untouched."""
    if isinstance(v, str) and "color" in key:
        return _to_color(v) or v
    return v


def _convert(value: Any, template: Any, key: str = "") -> Any:
    """Convert theme-JSON ``value`` to the type used by ``template`` (a map value)."""
    if template is None:
        return _guess(value, key)
    if isinstance(template, Color):
        return _to_color(value) or value
    if isinstance(template, bool):
        return bool(value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(template, float):
            return float(value)
        if isinstance(template, int) and float(value) == int(value):
            return int(value)
    if isinstance(template, dict) and isinstance(value, dict):
        fallback = next(iter(template.values()), None)  # e.g. another label preset
        out = {}
        for k, v in value.items():
            t = template.get(k, fallback if isinstance(fallback, dict) else None)
            out[k] = _convert(v, t, k)
        return out
    if isinstance(template, list) and isinstance(value, list):
        t0 = template[0] if template else None
        return [_convert(v, t0, key) for v in value]
    if isinstance(template, PoolColor) and isinstance(value, list):
        return PoolColor([_to_color(v) or v for v in value])
    if isinstance(template, PoolReal) and isinstance(value, list):
        return PoolReal([float(v) for v in value])
    return _guess(value, key) if isinstance(value, str) else value


def apply_theme(doc: MapDocument, theme_json: dict) -> None:
    """Overwrite ``root["theme"]`` keys present in both the map and ``theme_json``."""
    theme = doc.root["theme"]
    for key, value in theme_json.items():
        if key not in theme:
            continue
        theme[key] = _convert(copy.deepcopy(value), theme[key], key)
    _sync_names(theme, "ground_colors", "ground_color_names")
    _sync_names(theme, "water_colors", "water_color_names")


def _sync_names(theme: dict, colors_key: str, names_key: str) -> None:
    """Wonderdraft pairs names with colours by index: trim or pad names to match."""
    if names_key not in theme or colors_key not in theme:
        return
    names, n = list(theme[names_key]), len(theme[colors_key])
    names = names[:n] + [f"Color {i}" for i in range(len(names) + 1, n + 1)]
    theme[names_key] = names
