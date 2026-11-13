"""Decode Godot GCPF (File.open_compressed) containers. Mode 0 = FastLZ."""
import struct, sys, zlib

def fastlz_decompress(src, maxout):
    level = (src[0] >> 5) + 1
    ip, op, out = 0, 0, bytearray()
    ctrl = src[ip] & 31; ip += 1
    n = len(src)
    while True:
        if ctrl >= 32:
            length = (ctrl >> 5) - 1
            ofs = (ctrl & 31) << 8
            ref = len(out) - ofs - 1
            if length == 6:
                if level == 1:
                    length += src[ip]; ip += 1
                else:
                    while True:
                        c = src[ip]; ip += 1; length += c
                        if c != 255: break
            if level == 1:
                ref -= src[ip]; ip += 1
            else:
                code = src[ip]; ip += 1
                ref -= code
                if code == 255 and ofs == (31 << 8):
                    ofs = (src[ip] << 8) | src[ip + 1]; ip += 2
                    ref = len(out) - ofs - 8191 - 1
            length += 3
            for i in range(length):
                out.append(out[ref + i])
        else:
            ctrl += 1
            out += src[ip:ip + ctrl]; ip += ctrl
        if ip >= n: break
        ctrl = src[ip]; ip += 1
    return bytes(out)

def read_gcpf(path):
    d = open(path, 'rb').read()
    assert d[:4] == b'GCPF', d[:4]
    mode, block, total = struct.unpack_from('<III', d, 4)
    nblocks = (total + block - 1) // block  # Godot writes ceil(total/block) sizes (+1 if exact multiple)
    if total % block == 0: nblocks += 1
    sizes = struct.unpack_from('<%dI' % nblocks, d, 16)
    p = 16 + 4 * nblocks
    out = bytearray()
    for i, s in enumerate(sizes):
        chunk = d[p:p + s]; p += s
        want = min(block, total - len(out))
        if mode == 0:
            out += fastlz_decompress(chunk, want)
        elif mode == 1:
            out += zlib.decompress(chunk)
        else:
            raise NotImplementedError(mode)
    assert len(out) == total, (len(out), total)
    assert p == len(d) or (p + 4 == len(d) and d[-4:] == b"GCPF"), (p, len(d))
    return mode, block, bytes(out)

if __name__ == '__main__':
    mode, block, data = read_gcpf(sys.argv[1])
    open(sys.argv[2], 'wb').write(data)
    print('mode', mode, 'block', block, 'bytes', len(data))
