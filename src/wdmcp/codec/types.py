"""Typed values for the Godot 3 Variants that appear in Wonderdraft maps.

PoolByteArray is plain ``bytes``; Variant dictionaries/arrays are ``dict``/``list``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Vector2:
    x: float
    y: float


@dataclass(frozen=True)
class Rect2:
    position: Vector2
    size: Vector2


@dataclass(frozen=True)
class Color:
    r: float
    g: float
    b: float
    a: float = 1.0


@dataclass(frozen=True)
class Transform2D:
    x: Vector2
    y: Vector2
    origin: Vector2


@dataclass(frozen=True)
class NodePath:
    names: list[str] = field(default_factory=list)
    subnames: list[str] = field(default_factory=list)
    absolute: bool = False


@dataclass(frozen=True)
class GodotObject:
    """An Object variant. ``cls == ""`` is Godot's null Object.

    ``props`` maps name -> value (last occurrence wins). Wonderdraft's ImageTexture
    objects serialise the property name ``flags`` twice, so when a decoded object
    contains duplicate names ``pairs`` keeps the full ordered (name, value) list for
    byte-exact re-encoding; it is ignored by the encoder if ``props`` was edited.
    """

    cls: str
    props: dict = field(default_factory=dict)
    pairs: list[tuple[str, Any]] | None = None


@dataclass(frozen=True)
class PoolReal:
    values: list[float] = field(default_factory=list)


@dataclass(frozen=True)
class PoolInt:
    values: list[int] = field(default_factory=list)


@dataclass(frozen=True)
class PoolColor:
    values: list[Color] = field(default_factory=list)


@dataclass(frozen=True)
class PoolVector2:
    values: list[Vector2] = field(default_factory=list)


@dataclass(frozen=True)
class PoolString:
    values: list[str] = field(default_factory=list)
