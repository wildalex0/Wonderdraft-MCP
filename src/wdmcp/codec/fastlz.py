"""FastLZ block codec: levels 1 and 2 decode, level 1 encode.

Level-1 stream format (FastLZ by Ariya Hidayat, MIT; implemented here from the format):

* Literal run: control byte ``000LLLLL`` (``L = count - 1``, 1..32 bytes), then the bytes.
* Short match: control byte ``LLLDDDDD`` with ``LLL = length - 2`` (1..6, length 3..8) and
  ``DDDDD`` the high 5 bits of ``distance - 1``, then the low 8 bits of ``distance - 1``.
* Long match: ``LLL = 7``, then an extra byte ``length - 9`` (length 9..264), then the low 8
  bits of ``distance - 1``. Distance is 1..8192; a match may overlap its own output.
* The top 3 bits of the very first byte carry the level (0 = level 1), so the first
  instruction is always a literal run.
"""

MAX_LITERAL = 32
MIN_MATCH = 3
MAX_MATCH = 264         # 6 + 255 + 3
MAX_DISTANCE = 8191     # the reference encoder stays below 8192


def decompress(src: bytes) -> bytes:
    """Decode one FastLZ block. The level is taken from the top 3 bits of byte 0."""
    n = len(src)
    if n == 0:
        return b""
    level = (src[0] >> 5) + 1
    if level not in (1, 2):
        raise ValueError(f"unsupported FastLZ level {level}")
    out = bytearray()
    ip = 0
    try:
        while ip < n:
            ctrl = src[ip]
            if ip == 0:
                ctrl &= 31  # first byte's top bits carry the level, not a match
            ip += 1
            if ctrl >= 32:
                length = (ctrl >> 5) - 1
                ofs = (ctrl & 31) << 8
                if length == 6:
                    if level == 1:
                        length += src[ip]
                        ip += 1
                    else:
                        while True:
                            c = src[ip]
                            ip += 1
                            length += c
                            if c != 255:
                                break
                dist = ofs + 1
                if level == 1:
                    dist += src[ip]
                    ip += 1
                else:
                    code = src[ip]
                    ip += 1
                    dist += code
                    if code == 255 and ofs == (31 << 8):
                        ofs = (src[ip] << 8) | src[ip + 1]
                        ip += 2
                        dist = ofs + 8191 + 1
                length += 3
                start = len(out) - dist
                if start < 0:
                    raise ValueError("FastLZ back-reference before start of output")
                if dist >= length:
                    out += out[start:start + length]
                else:
                    pattern = bytes(out[start:])
                    reps = -(-length // dist)
                    out += (pattern * reps)[:length]
            else:
                ctrl += 1
                if ip + ctrl > n:
                    raise ValueError("FastLZ literal run overruns input")
                out += src[ip:ip + ctrl]
                ip += ctrl
    except IndexError:
        raise ValueError("truncated FastLZ stream") from None
    return bytes(out)


def _emit_literals(out: bytearray, src: bytes, start: int, end: int) -> None:
    while start < end:
        k = min(end - start, MAX_LITERAL)
        out.append(k - 1)
        out += src[start:start + k]
        start += k


def _emit_match(out: bytearray, length: int, dist: int) -> None:
    """One match instruction, ``MIN_MATCH <= length <= MAX_MATCH``, ``1 <= dist <= 8192``."""
    d = dist - 1
    if length <= 8:
        out.append(((length - 2) << 5) | (d >> 8))
        out.append(d & 255)
    else:
        out.append((7 << 5) | (d >> 8))
        out.append(length - 9)
        out.append(d & 255)


def _match_length(src: bytes, ref: int, ip: int, limit: int) -> int:
    """Length of the common run of src[ref:] and src[ip:] (ref < ip, overlap allowed),
    capped so that ``ip + length <= limit``; known to be >= MIN_MATCH. Gallops, then
    bisects, using slice comparisons so the byte compares run in C."""
    cap = limit - ip
    lo = MIN_MATCH            # prefix of length lo is known to match
    step = 16
    hi = lo + step
    while hi <= cap and src[ref + lo:ref + hi] == src[ip + lo:ip + hi]:
        lo = hi
        step <<= 1
        hi = lo + step
    hi = min(hi, cap + 1)     # prefix of length hi does not match (or is past the cap)
    while hi - lo > 1:
        mid = (lo + hi) >> 1
        if src[ref + lo:ref + mid] == src[ip + lo:ip + mid]:
            lo = mid
        else:
            hi = mid
    return lo


def compress(src: bytes) -> bytes:
    """Encode as a FastLZ level-1 stream (greedy, hash table of 3-byte sequences).

    As in the reference encoder, the first instruction is a literal run and the final byte
    is always a literal. Any input length is accepted (Godot pads blocks shorter than 16
    bytes itself; gcpf.write does the same)."""
    src = bytes(src)
    n = len(src)
    out = bytearray()
    if n < MIN_MATCH + 2:
        _emit_literals(out, src, 0, n)
        return bytes(out)
    limit = n - 1                   # matches end before the last byte
    last_key = limit - MIN_MATCH    # last position whose 3-byte key lies before ``limit``
    htab: dict[bytes, int] = {src[0:3]: 0}
    get = htab.get
    lit = 0
    ip = 1                          # byte 0 is always a literal
    while ip <= last_key:
        key = src[ip:ip + 3]
        ref = get(key)
        htab[key] = ip
        if ref is None or ip - ref > MAX_DISTANCE:
            ip += 1
            continue
        length = _match_length(src, ref, ip, limit)
        _emit_literals(out, src, lit, ip)
        dist = ip - ref
        r = length
        while r:
            take = MAX_MATCH if r > MAX_MATCH else r
            if r - take and r - take < MIN_MATCH:
                take = r - MIN_MATCH    # never leave a 1-2 byte tail
            _emit_match(out, take, dist)
            r -= take
        ip += length
        lit = ip
        # key the last two positions of the match so a following match can chain on
        for p in range(ip - 2, min(ip, last_key + 1)):
            htab[src[p:p + 3]] = p
    _emit_literals(out, src, lit, n)
    return bytes(out)
