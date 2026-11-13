"""Reader for Godot 3 GDPC v1 (unencrypted) .pck archives."""
from __future__ import annotations

import struct
from pathlib import Path


class PckReader:
    def __init__(self, path):
        self.path = Path(path)
        self._index: dict[str, tuple[int, int]] = {}
        with open(self.path, "rb") as f:
            head = f.read(4 + 16 + 64 + 4)
            if head[:4] != b"GDPC":
                raise ValueError(f"not a GDPC pck: {self.path}")
            version = struct.unpack("<I", head[4:8])[0]
            if version != 1:
                raise ValueError(f"unsupported pck version {version}")
            (count,) = struct.unpack("<I", head[84:88])
            for _ in range(count):
                (n,) = struct.unpack("<I", f.read(4))
                name = f.read(n).split(b"\0", 1)[0].decode("utf-8")
                off, size = struct.unpack("<QQ", f.read(16))
                f.read(16)  # md5
                self._index[name] = (off, size)

    def paths(self) -> list[str]:
        return list(self._index)

    def __contains__(self, res_path: str) -> bool:
        return res_path in self._index

    def read(self, res_path: str) -> bytes:
        try:
            off, size = self._index[res_path]
        except KeyError:
            raise KeyError(res_path) from None
        with open(self.path, "rb") as f:
            f.seek(off)
            return f.read(size)
