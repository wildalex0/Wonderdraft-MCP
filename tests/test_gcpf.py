import struct
from pathlib import Path

import pytest

from wdmcp.codec import fastlz, gcpf

FIX = Path(__file__).parent / "fixtures" / "testmap.wonderdraft_map"


@pytest.fixture(scope="module")
def fixture_bytes():
    return FIX.read_bytes()


@pytest.fixture(scope="module")
def payload(fixture_bytes):
    return gcpf.read(fixture_bytes)


def test_read_testmap_size(payload):
    assert len(payload) == 25_791_944


def test_write_read_roundtrip(payload):
    out = gcpf.write(payload)
    assert out[:4] == b"GCPF" and out[-4:] == b"GCPF"
    assert struct.unpack_from("<III", out, 4) == (0, 4096, len(payload))
    assert gcpf.read(out) == payload


def test_fixture_header_block_count_rule(fixture_bytes, payload):
    mode, block, total = struct.unpack_from("<III", fixture_bytes, 4)
    assert (mode, block, total) == (0, 4096, len(payload))
    nblocks = -(-total // block) + (1 if total % block == 0 else 0)
    sizes = struct.unpack_from(f"<{nblocks}I", fixture_bytes, 16)
    assert 16 + 4 * nblocks + sum(sizes) + 4 == len(fixture_bytes)
    assert fixture_bytes[-4:] == b"GCPF"


def test_fastlz_roundtrip_small_and_edge():
    for s in [b"", b"a", b"x" * 4096, bytes(range(256)) * 16]:
        assert fastlz.decompress(fastlz.compress(s)) == s


def test_fastlz_decompress_overlapping_match_levels_1_and_2():
    # literal "ab", then a 5-byte match at distance 2 (overlapping copy)
    assert fastlz.decompress(bytes([0x01, 0x61, 0x62, 0x60, 0x01])) == b"abababa"
    assert fastlz.decompress(bytes([0x21, 0x61, 0x62, 0x60, 0x01])) == b"abababa"


def test_fastlz_truncated_raises():
    with pytest.raises(ValueError):
        fastlz.decompress(bytes([0x05, 0x61]))


def test_gcpf_exact_block_size_payload():
    p = bytes(range(256)) * 16
    assert len(p) == 4096
    out = gcpf.write(p)
    # one block size plus the extra entry for exact multiples (16-byte padded, never 0)
    assert struct.unpack_from("<II", out, 16)[1] != 0
    assert gcpf.read(out) == p


AUTOSAVE = Path(__file__).parent / "fixtures" / "autosave_exact_multiple.wonderdraft_map"


def test_read_autosave_exact_multiple():
    assert len(gcpf.read(AUTOSAVE.read_bytes())) == 24_887_296


@pytest.mark.parametrize("n", [4096, 4096 + 5])
def test_write_last_size_entry_nonzero(n):
    p = bytes(range(256)) * 17
    p = p[:n]
    out = gcpf.write(p)
    nblocks = -(-n // 4096) + (1 if n % 4096 == 0 else 0)
    sizes = struct.unpack_from(f"<{nblocks}I", out, 16)
    assert sizes[-1] != 0
    assert gcpf.read(out) == p


def test_short_block_decoding_longer_is_truncated_but_shorter_is_error():
    # 16 zero bytes decoded for an expected 0-byte extra block is accepted
    out = gcpf.write(b"")
    assert gcpf.read(out) == b""
    short = bytearray(gcpf.write(b"x" * 100))
    # claim 101 bytes but only 100 decode from the block -> error
    struct.pack_into("<I", short, 12, 101)
    with pytest.raises(gcpf.GcpfError):
        gcpf.read(bytes(short))


def test_gcpf_non_multiple_and_empty():
    for p in [b"", b"hello", b"z" * 4097, b"q" * 8192]:
        assert gcpf.read(gcpf.write(p)) == p


def test_bad_magic_raises():
    with pytest.raises(gcpf.GcpfError):
        gcpf.read(b"NOPE" + b"\0" * 32)


def test_truncated_raises(fixture_bytes):
    with pytest.raises(gcpf.GcpfError):
        gcpf.read(fixture_bytes[:1000])


# ---------------------------------------------------------------- level-1 compressor
def _level1_ops(z: bytes):
    """Walk a level-1 stream; yield ("lit", n) / ("match", length, dist). Checks strictness."""
    ip, first = 0, True
    while ip < len(z):
        ctrl = z[ip]
        if first:
            assert ctrl >> 5 == 0, "first byte must carry level 1 (top bits 0)"
            first = False
        ip += 1
        if ctrl < 32:
            yield ("lit", ctrl + 1)
            ip += ctrl + 1
        else:
            length = (ctrl >> 5) + 2
            if ctrl >> 5 == 7:
                length = 9 + z[ip]
                ip += 1
            dist = ((ctrl & 31) << 8) + z[ip] + 1
            ip += 1
            yield ("match", length, dist)
    assert ip == len(z)


import os  # noqa: E402
import random  # noqa: E402

_rng = random.Random(7)
EDGE_LENGTHS = [0, 1, 2, 3, 4, 5, 8, 9, 15, 16, 17, 31, 32, 33, 263, 264, 265, 266, 267, 268,
                527, 528, 529, 530, 4095, 4096, 4097, 8191, 8192, 8193, 8194, 20000]


@pytest.mark.parametrize("n", EDGE_LENGTHS)
def test_compress_roundtrip_edge_lengths(n):
    for data in (b"q" * n, bytes(_rng.choice(b"ab") for _ in range(n)), os.urandom(n),
                 (b"RGBA" * (n // 4 + 1))[:n]):
        z = fastlz.compress(data)
        assert fastlz.decompress(z) == data
        ops = list(_level1_ops(z))
        if data:
            assert ops[0][0] == "lit" and ops[-1][0] == "lit"
        for op in ops:
            if op[0] == "match":
                assert 3 <= op[1] <= 264 and 1 <= op[2] <= 8191


def test_compress_roundtrip_kinds():
    cases = [b"", b"a", os.urandom(4096), b"x" * 4096, bytes(range(256)) * 16,
             b"abc" * 1000 + os.urandom(100) + b"abc" * 1000,
             os.urandom(5000) * 3,                    # repeats beyond 4096, near the 8191 cap
             os.urandom(9000) * 2,                    # repeat distance > 8191: no match allowed
             b"\x00" * 100_000]                        # long run over many 264-byte matches
    for data in cases:
        assert fastlz.decompress(fastlz.compress(data)) == data


def test_compress_long_runs_use_matches():
    z = fastlz.compress(b"x" * 4096)
    assert len(z) < 60
    ops = list(_level1_ops(z))
    assert sum(1 for o in ops if o[0] == "match" and o[1] == 264) >= 15


def test_compress_testmap_blocks_roundtrip(fixture_bytes, payload):
    blocks = [payload[i:i + 4096] for i in range(0, 4096 * 400, 4096)]
    for b in blocks[::7]:
        assert fastlz.decompress(fastlz.compress(b)) == b


def test_write_is_much_smaller_than_payload(payload):
    out = gcpf.write(payload)
    assert len(out) < 0.15 * len(payload)
    assert gcpf.read(out) == payload


@pytest.mark.parametrize("name", ["testmap.wonderdraft_map", "autosave_exact_multiple.wonderdraft_map",
                                  "probe_gui.wonderdraft_map"])
def test_byte_exact_gate_every_fixture(name):
    p = gcpf.read((Path(__file__).parent / "fixtures" / name).read_bytes())
    assert gcpf.read(gcpf.write(p)) == p
