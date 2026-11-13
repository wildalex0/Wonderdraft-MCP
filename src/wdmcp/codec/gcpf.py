"""Godot FileAccessCompressed ("GCPF") container, FastLZ (mode 0) and zlib (mode 1)."""
import struct
import zlib

from . import fastlz

MAGIC = b"GCPF"
MODE_FASTLZ = 0
MODE_ZLIB = 1


class GcpfError(ValueError):
    """Raised for malformed or unsupported GCPF data."""


def _block_count(total: int, block: int) -> int:
    # Godot 3 writes ceil(total/block) sizes, plus one extra when total is an exact multiple.
    n = -(-total // block)
    if total % block == 0:
        n += 1
    return n


def read(data: bytes) -> bytes:
    if len(data) < 16 or data[:4] != MAGIC:
        raise GcpfError("not a GCPF container (bad magic)")
    mode, block, total = struct.unpack_from("<III", data, 4)
    if mode not in (MODE_FASTLZ, MODE_ZLIB):
        raise GcpfError(f"unsupported GCPF compression mode {mode}")
    if block == 0:
        raise GcpfError("GCPF block size is zero")
    nblocks = _block_count(total, block)
    if 16 + 4 * nblocks > len(data):
        raise GcpfError("truncated GCPF header")
    sizes = struct.unpack_from(f"<{nblocks}I", data, 16)
    pos = 16 + 4 * nblocks
    if pos + sum(sizes) > len(data):
        raise GcpfError("truncated GCPF data")
    out = bytearray()
    try:
        for i, size in enumerate(sizes):
            chunk = data[pos:pos + size]
            pos += size
            want = min(block, max(total - i * block, 0))
            dec = fastlz.decompress(chunk) if mode == MODE_FASTLZ else zlib.decompress(chunk)
            if len(dec) < want:
                raise ValueError(f"block {i} decoded {len(dec)} bytes, expected {want}")
            # Godot pads blocks shorter than 16 bytes to 16 before compressing; drop the padding.
            out += dec[:want]
    except (ValueError, zlib.error) as e:
        raise GcpfError(f"corrupt GCPF block: {e}") from e
    if len(out) != total:
        raise GcpfError(f"GCPF size mismatch: header {total}, decoded {len(out)}")
    if data[pos:] not in (b"", MAGIC):
        raise GcpfError("bad GCPF trailer")
    return bytes(out)


def write(payload: bytes, block_size: int = 4096) -> bytes:
    if block_size <= 0:
        raise GcpfError("block_size must be positive")
    total = len(payload)
    chunks = [payload[i:i + block_size] for i in range(0, total, block_size)]
    if total % block_size == 0:
        chunks.append(b"")
    # Godot zero-pads any block shorter than 16 bytes to 16 before compressing. Rasters
    # repeat whole blocks (open sea, plain ground), so identical blocks are compressed once.
    memo: dict[bytes, bytes] = {}
    blocks = []
    for c in chunks:
        c = c.ljust(16, b"\x00")
        z = memo.get(c)
        if z is None:
            z = memo[c] = fastlz.compress(c)
        blocks.append(z)
    head = MAGIC + struct.pack("<III", MODE_FASTLZ, block_size, total)
    sizes = struct.pack(f"<{len(blocks)}I", *map(len, blocks))
    return head + sizes + b"".join(blocks) + MAGIC
