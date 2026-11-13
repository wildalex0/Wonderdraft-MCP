"""Wonderdraft map codec: GCPF container + Godot 3 Variant payload."""
from pathlib import Path

from . import gcpf, variant


def load_map_bytes(path) -> dict:
    """Read a .wonderdraft_map file into its root dictionary."""
    return variant.decode(gcpf.read(Path(path).read_bytes()))


def dump_map_bytes(root: dict) -> bytes:
    """Serialize a root dictionary to .wonderdraft_map file bytes."""
    return gcpf.write(variant.encode(root))


__all__ = ["load_map_bytes", "dump_map_bytes"]
