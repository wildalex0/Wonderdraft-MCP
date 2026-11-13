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
