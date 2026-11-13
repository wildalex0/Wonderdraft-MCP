from pathlib import Path

import pytest

from wdmcp.codec import dump_map_bytes, gcpf, load_map_bytes, variant
from wdmcp.codec.types import (
    Color, GodotObject, NodePath, PoolColor, PoolInt, PoolReal, PoolString,
    PoolVector2, Rect2, Transform2D, Vector2,
)

FIX = Path(__file__).parent / "fixtures" / "testmap.wonderdraft_map"


def test_testmap_byte_exact():
    payload = gcpf.read(FIX.read_bytes())
    assert variant.encode(variant.decode(payload)) == payload


def test_autosave_byte_exact():
    fix = Path(__file__).parent / "fixtures" / "autosave_exact_multiple.wonderdraft_map"
    payload = gcpf.read(fix.read_bytes())
    assert variant.encode(variant.decode(payload)) == payload


def test_real_width_rule():  # Godot: 64-bit iff float32 can't hold it exactly
    assert len(variant.encode(0.5)) == 4 + 8 and len(variant.encode(0.47001155663049116)) == 4 + 12


def test_int_width_rule():
    # Wonderdraft stores every int as int64 (even version=15), unlike stock Godot's value-based rule.
    assert len(variant.encode(15)) == 4 + 12 and len(variant.encode(4227431513)) == 4 + 12
    assert variant.decode(variant.encode(-(1 << 40))) == -(1 << 40)


def test_decode_accepts_int32():
    import struct
    body = struct.pack("<Ii", 2, -7)
    assert variant.decode(struct.pack("<I", len(body)) + body) == -7


def test_known_values():
    root = variant.decode(gcpf.read(FIX.read_bytes()))
    assert root["version"] == 15 and root["map_width"] == 1920.0
    assert root["paths"][0]["noise_seed"] == 4227431513
    assert root["mask"].cls == "Image" and root["mask"].props["data"]["width"] == 1920


def test_unicode_label_roundtrip():
    v = {"text": "Ödmark Straße ✦"}
    assert variant.decode(variant.encode(v)) == v


def test_all_types_roundtrip():
    v = {
        "n": None, "t": True, "f": False, "i": -5, "big": -(1 << 40), "r": 1.5, "d": 0.1,
        "v": Vector2(0.5, 2.0), "rect": Rect2(Vector2(1, 2), Vector2(3, 4)),
        "xf": Transform2D(Vector2(1, 0), Vector2(0, 1), Vector2(5, 6)),
        "c": Color(0.25, 0.5, 0.75, 1.0),
        "np": NodePath(["root", "a"], ["prop"], True),
        "obj": GodotObject("Image", {"x": [1, "s"]}), "null_obj": GodotObject(""),
        "pb": b"\x01\x02\x03", "pi": PoolInt([1, -2]), "pr": PoolReal([0.5, 2.0]),
        "pc": PoolColor([Color(0, 0.5, 1, 1)]), "pv": PoolVector2([Vector2(1, 2)]),
        "ps": PoolString(["a", "bé"]),
    }
    assert variant.decode(variant.encode(v)) == v


def test_decode_rejects_bad_length_prefix():
    payload = variant.encode(1)
    with pytest.raises(variant.VariantError):
        variant.decode(payload + b"\0\0\0\0")


def test_unsupported_type_raises():
    with pytest.raises(variant.VariantError):
        variant.encode(object())


def test_map_file_roundtrip(tmp_path):
    root = load_map_bytes(FIX)
    out = tmp_path / "m.wonderdraft_map"
    out.write_bytes(dump_map_bytes(root))
    assert load_map_bytes(out) == root


def test_object_duplicate_property_names_preserved():
    dup = GodotObject("ImageTexture", {"flags": 2, "x": 1}, [("flags", 7), ("x", 1), ("flags", 2)])
    out = variant.encode(dup)
    assert variant.decode(out) == dup
    assert out.count(b"flags") == 2
    # editing props invalidates the stale pair list instead of being silently ignored
    edited = GodotObject("ImageTexture", {"flags": 3, "x": 1}, dup.pairs)
    assert variant.decode(variant.encode(edited)) == GodotObject("ImageTexture", {"flags": 3, "x": 1})
