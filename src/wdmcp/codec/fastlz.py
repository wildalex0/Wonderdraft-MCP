"""FastLZ block codec (levels 1 and 2 decode; level 1 literal-only encode)."""


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


def compress(src: bytes) -> bytes:
    """Encode as a valid level-1 stream using literal runs only (no matching)."""
    out = bytearray()
    for i in range(0, len(src), 32):
        run = src[i:i + 32]
        out.append(len(run) - 1)
        out += run
    return bytes(out)
