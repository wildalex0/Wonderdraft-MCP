"""Godot 3.x binary Variant (marshalls.cpp) decoder and mirror encoder."""
from __future__ import annotations

import struct
from typing import Any

from .types import (
    Color, GodotObject, NodePath, PoolColor, PoolInt, PoolReal, PoolString,
    PoolVector2, Rect2, Transform2D, Vector2,
)

NIL, BOOL, INT, REAL, STRING, VECTOR2, RECT2 = 0, 1, 2, 3, 4, 5, 6
TRANSFORM2D, COLOR, NODE_PATH, OBJECT, DICTIONARY, ARRAY = 8, 14, 15, 17, 18, 19
POOL_BYTE, POOL_INT, POOL_REAL, POOL_STRING, POOL_VECTOR2, POOL_COLOR = 20, 21, 22, 23, 24, 26

_NODEPATH_NEW = 0x80000000


class VariantError(ValueError):
    pass


# ---------------------------------------------------------------- decoding

def _u32(b: bytes, p: int) -> int:
    return struct.unpack_from("<I", b, p)[0]


def _string(b: bytes, p: int) -> tuple[str, int]:
    n = _u32(b, p)
    p += 4
    if p + n > len(b):
        raise VariantError("string overruns buffer at %d" % p)
    return b[p:p + n].decode("utf-8"), p + n + (-n % 4)


def _floats(b: bytes, p: int, n: int, f64: bool) -> tuple[tuple[float, ...], int]:
    return struct.unpack_from("<%d%s" % (n, "d" if f64 else "f"), b, p), p + n * (8 if f64 else 4)


def _dec(b: bytes, p: int) -> tuple[Any, int]:
    h = _u32(b, p)
    p += 4
    t, f64 = h & 0xFFFF, bool(h >> 16 & 1)
    if t == NIL:
        return None, p
    if t == BOOL:
        return bool(_u32(b, p)), p + 4
    if t == INT:
        if f64:
            return struct.unpack_from("<q", b, p)[0], p + 8
        return struct.unpack_from("<i", b, p)[0], p + 4
    if t == REAL:
        v, p = _floats(b, p, 1, f64)
        return v[0], p
    if t == STRING:
        return _string(b, p)
    if t == VECTOR2:
        v, p = _floats(b, p, 2, f64)
        return Vector2(*v), p
    if t == RECT2:
        v, p = _floats(b, p, 4, f64)
        return Rect2(Vector2(v[0], v[1]), Vector2(v[2], v[3])), p
    if t == TRANSFORM2D:
        v, p = _floats(b, p, 6, f64)
        return Transform2D(Vector2(v[0], v[1]), Vector2(v[2], v[3]), Vector2(v[4], v[5])), p
    if t == COLOR:
        v, p = _floats(b, p, 4, False)
        return Color(*v), p
    if t == NODE_PATH:
        n = _u32(b, p)
        if not n & _NODEPATH_NEW:
            raise VariantError("old-format NodePath unsupported")
        nsub = _u32(b, p + 4)
        absolute = bool(_u32(b, p + 8) & 1)
        p += 12
        parts = []
        k = n & 0x7FFFFFFF
        for _ in range(k + nsub):
            s, p = _string(b, p)
            parts.append(s)
        return NodePath(parts[:k], parts[k:], absolute), p
    if t == OBJECT:
        if f64:
            raise VariantError("object-by-id unsupported")
        cls, p = _string(b, p)
        if not cls:  # Godot encodes a null Object as an empty class name, no properties
            return GodotObject(""), p
        cnt = _u32(b, p)
        p += 4
        props = {}
        pairs = []
        for _ in range(cnt):
            k, p = _string(b, p)
            v, p = _dec(b, p)
            props[k] = v
            pairs.append((k, v))
        return GodotObject(cls, props, pairs if len(props) != cnt else None), p
    if t == DICTIONARY:
        n = _u32(b, p) & 0x7FFFFFFF
        p += 4
        d = {}
        for _ in range(n):
            k, p = _dec(b, p)
            d[k], p = _dec(b, p)
        return d, p
    if t == ARRAY:
        n = _u32(b, p) & 0x7FFFFFFF
        p += 4
        a = []
        for _ in range(n):
            v, p = _dec(b, p)
            a.append(v)
        return a, p
    if t == POOL_BYTE:
        n = _u32(b, p)
        p += 4
        return bytes(b[p:p + n]), p + n + (-n % 4)
    if t in (POOL_INT, POOL_REAL):
        n = _u32(b, p)
        p += 4
        v = list(struct.unpack_from("<%d%s" % (n, "i" if t == POOL_INT else "f"), b, p))
        return (PoolInt if t == POOL_INT else PoolReal)(v), p + 4 * n
    if t == POOL_STRING:
        n = _u32(b, p)
        p += 4
        a = []
        for _ in range(n):
            s, p = _string(b, p)
            a.append(s)
        return PoolString(a), p
    if t in (POOL_VECTOR2, POOL_COLOR):
        k = 2 if t == POOL_VECTOR2 else 4
        n = _u32(b, p)
        p += 4
        v = struct.unpack_from("<%df" % (n * k), b, p)
        cls = Vector2 if t == POOL_VECTOR2 else Color
        wrap = PoolVector2 if t == POOL_VECTOR2 else PoolColor
        return wrap([cls(*v[i:i + k]) for i in range(0, n * k, k)]), p + 4 * n * k
    raise VariantError("unsupported variant type %d at offset %d" % (t, p - 4))


def decode(payload: bytes) -> Any:
    """Decode a u32 length-prefixed Variant payload (as returned by gcpf.read)."""
    if len(payload) < 4:
        raise VariantError("payload too short")
    n = _u32(payload, 0)
    if n != len(payload) - 4:
        raise VariantError("length prefix %d does not cover payload of %d bytes" % (n, len(payload) - 4))
    try:
        v, p = _dec(payload, 4)
    except struct.error as e:
        raise VariantError("truncated variant: %s" % e) from e
    if p != len(payload):
        raise VariantError("trailing data after variant at %d" % p)
    return v


# ---------------------------------------------------------------- encoding

def _hdr(t: int, flags: int = 0) -> bytes:
    return struct.pack("<I", t | flags << 16)


def _enc_str(s: str) -> bytes:
    raw = s.encode("utf-8")
    return struct.pack("<I", len(raw)) + raw + b"\0" * (-len(raw) % 4)


def _fits_f32(x: float) -> bool:
    try:
        return struct.unpack("<f", struct.pack("<f", x))[0] == x
    except OverflowError:
        return False


def _enc(v: Any) -> bytes:
    if v is None:
        return _hdr(NIL)
    if isinstance(v, bool):  # before int: bool is an int subclass
        return _hdr(BOOL) + struct.pack("<I", int(v))
    if isinstance(v, int):
        # Wonderdraft writes every int as int64 (all 196 ints in the fixture, including
        # small ones like version=15, carry the 64-bit flag), so mirror that for byte-exactness.
        return _hdr(INT, 1) + struct.pack("<q", v)
    if isinstance(v, float):
        if _fits_f32(v) or v != v:
            return _hdr(REAL) + struct.pack("<f", v)
        return _hdr(REAL, 1) + struct.pack("<d", v)
    if isinstance(v, str):
        return _hdr(STRING) + _enc_str(v)
    if isinstance(v, Vector2):
        return _hdr(VECTOR2) + struct.pack("<2f", v.x, v.y)
    if isinstance(v, Rect2):
        return _hdr(RECT2) + struct.pack("<4f", v.position.x, v.position.y, v.size.x, v.size.y)
    if isinstance(v, Transform2D):
        return _hdr(TRANSFORM2D) + struct.pack("<6f", v.x.x, v.x.y, v.y.x, v.y.y, v.origin.x, v.origin.y)
    if isinstance(v, Color):
        return _hdr(COLOR) + struct.pack("<4f", v.r, v.g, v.b, v.a)
    if isinstance(v, NodePath):
        out = [_hdr(NODE_PATH), struct.pack("<III", _NODEPATH_NEW | len(v.names), len(v.subnames), int(v.absolute))]
        out += [_enc_str(s) for s in (*v.names, *v.subnames)]
        return b"".join(out)
    if isinstance(v, GodotObject):
        if not v.cls:
            return _hdr(OBJECT) + _enc_str("")
        items = list(v.props.items())
        if v.pairs is not None and {k: x for k, x in v.pairs} == v.props:
            items = v.pairs  # duplicate property names preserved from decode
        out = [_hdr(OBJECT), _enc_str(v.cls), struct.pack("<I", len(items))]
        for k, x in items:
            out += [_enc_str(k), _enc(x)]
        return b"".join(out)
    if isinstance(v, dict):
        out = [_hdr(DICTIONARY), struct.pack("<I", len(v))]
        for k, x in v.items():
            out += [_enc(k), _enc(x)]
        return b"".join(out)
    if isinstance(v, (list, tuple)):
        return _hdr(ARRAY) + struct.pack("<I", len(v)) + b"".join(_enc(x) for x in v)
    if isinstance(v, (bytes, bytearray)):
        return _hdr(POOL_BYTE) + struct.pack("<I", len(v)) + bytes(v) + b"\0" * (-len(v) % 4)
    if isinstance(v, PoolInt):
        return _hdr(POOL_INT) + struct.pack("<I%di" % len(v.values), len(v.values), *v.values)
    if isinstance(v, PoolReal):
        return _hdr(POOL_REAL) + struct.pack("<I%df" % len(v.values), len(v.values), *v.values)
    if isinstance(v, PoolString):
        return _hdr(POOL_STRING) + struct.pack("<I", len(v.values)) + b"".join(_enc_str(s) for s in v.values)
    if isinstance(v, PoolVector2):
        flat = [c for e in v.values for c in (e.x, e.y)]
        return _hdr(POOL_VECTOR2) + struct.pack("<I%df" % len(flat), len(v.values), *flat)
    if isinstance(v, PoolColor):
        flat = [c for e in v.values for c in (e.r, e.g, e.b, e.a)]
        return _hdr(POOL_COLOR) + struct.pack("<I%df" % len(flat), len(v.values), *flat)
    raise VariantError("cannot encode %s" % type(v).__name__)


def encode(value: Any) -> bytes:
    """Encode a value as a u32 length-prefixed Variant payload."""
    body = _enc(value)
    return struct.pack("<I", len(body)) + body
