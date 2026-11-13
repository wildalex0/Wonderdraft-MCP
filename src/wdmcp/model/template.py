"""Entry factories. Keys and defaults are copied from entries in a real
Wonderdraft 1.1.8 map (hard-coded; the fixture is not read at runtime)."""
from wdmcp.codec.types import Color, Vector2
from .points import format_points

_WHITE = Color(1.0, 1.0, 1.0, 1.0)


def _v(p) -> Vector2:
    return p if isinstance(p, Vector2) else Vector2(float(p[0]), float(p[1]))


def symbol_entry(texture: str, pos, *, rotation: float = 0.0, scale=(1.0, 1.0),
                 z_index: int = 0, offset=(0.0, -25.0), radius: float = 43.0,
                 type: str = "symbol", mirror: bool = False,
                 outline_width: int = 0, outline_color: Color = _WHITE,
                 sample: Color | None = None, custom_colors=None,
                 custom_color_mode=None) -> dict:
    return {
        "texture": texture, "position": _v(pos), "rotation": float(rotation),
        "scale": _v(scale), "z_index": z_index, "offset": _v(offset),
        "radius": float(radius), "type": type, "mirror": mirror,
        "outline_width": outline_width, "outline_color": outline_color,
        "sample": sample, "custom_colors": custom_colors,
        "custom_color_mode": custom_color_mode,
    }


def label_entry(text: str, pos, *, font: str = "IM FELL DW Pica", size: int = 28,
                color: Color = Color(0.2421880066394806, 0.2088170051574707, 0.12014800310134888, 1.0),
                outline_size: int = 3,
                outline_color: Color = Color(0.8398439884185791, 0.7940940260887146, 0.672531008720398, 0.6349800229072571),
                glow_size: int = 0, glow_color: Color = _WHITE, align: int = 1,
                curve: float = 0.0, extra_spacing_char: int = 0, rotation: float = 0.0,
                z_index: int = 0) -> dict:
    return {
        "position": _v(pos), "rotation": float(rotation), "text": text, "font": font,
        "size": size, "color": color, "outline_size": outline_size,
        "outline_color": outline_color, "glow_size": glow_size,
        "glow_color": glow_color, "align": align, "curve": float(curve),
        "extra_spacing_char": extra_spacing_char, "z_index": z_index,
    }


def path_entry(points, *, style: str = "res://textures/paths/path_blended",
               color: Color = Color(0.296875, 0.19399100542068481, 0.10321000218391418, 1.0),
               width: float = 4.0, roughness: float = 0.33, noise_seed: int = 0,
               z_index: int = 0, straight: bool = False) -> dict:
    return {
        "position": Vector2(0.0, 0.0), "points": format_points(points),
        "color": color, "style": style, "width": float(width),
        "roughness": float(roughness), "noise_seed": noise_seed,
        "z_index": z_index, "straight": straight,
    }


def territory_entry(points, *, color: Color = Color(0.0, 0.3405759930610657, 0.703125, 1.0),
                    smoothing: float = 0.2, opacity: float = 0.25,
                    style: str = "res://textures/borders/border_dash",
                    width: float = 10.0, z_index: int = 0) -> dict:
    return {
        "position": Vector2(0.0, 0.0), "points": format_points(points),
        "smoothing": float(smoothing), "color": color, "opacity": float(opacity),
        "style": style, "width": float(width), "z_index": z_index,
    }
