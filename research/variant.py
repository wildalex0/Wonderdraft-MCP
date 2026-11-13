"""Godot 3.x binary Variant (marshalls.cpp) decoder + schema dumper."""
import struct, sys, json

T = ['NIL','BOOL','INT','REAL','STRING','VECTOR2','RECT2','VECTOR3','TRANSFORM2D','PLANE','QUAT','AABB','BASIS',
     'TRANSFORM','COLOR','NODE_PATH','RID','OBJECT','DICTIONARY','ARRAY','POOL_BYTE','POOL_INT','POOL_REAL',
     'POOL_STRING','POOL_VECTOR2','POOL_VECTOR3','POOL_COLOR']

class Bytes(bytes):
    pass

def dec(b, p):
    h = struct.unpack_from('<I', b, p)[0]; p += 4
    t, flags = h & 0xFFFF, h >> 16
    f64 = flags & 1
    fl = lambda n: (struct.unpack_from('<%dd' % n, b, p) if f64 else struct.unpack_from('<%df' % n, b, p))
    sz = 8 if f64 else 4
    if t == 0: return None, p
    if t == 1: return bool(struct.unpack_from('<I', b, p)[0]), p + 4
    if t == 2:
        if f64: return struct.unpack_from('<q', b, p)[0], p + 8
        return struct.unpack_from('<i', b, p)[0], p + 4
    if t == 3: return fl(1)[0], p + sz
    if t == 4:
        n = struct.unpack_from('<I', b, p)[0]; p += 4
        s = b[p:p + n].decode('utf-8', 'replace'); p += n + (-n % 4)
        return s, p
    if t == 5: return ('V2', fl(2)), p + 2 * sz
    if t == 6: return ('RECT2', fl(4)), p + 4 * sz
    if t == 7: return ('V3', fl(3)), p + 3 * sz
    if t == 8: return ('T2D', fl(6)), p + 6 * sz
    if t == 14: return ('COLOR', struct.unpack_from('<4f', b, p)), p + 16
    if t == 15:  # node path (new format)
        n = struct.unpack_from('<I', b, p)[0]; p += 4
        assert n & 0x80000000
        sub = struct.unpack_from('<I', b, p)[0]; p += 8
        names = []
        for _ in range((n & 0x7FFFFFFF) + sub):
            l = struct.unpack_from('<I', b, p)[0]; p += 4
            names.append(b[p:p + l].decode()); p += l + (-l % 4)
        return ('NODEPATH', names), p
    if t == 17:
        # object: class name + props (full objects encoded)
        if flags & 1:
            return ('OBJID', struct.unpack_from('<Q', b, p)[0]), p + 8
        n = struct.unpack_from('<I', b, p)[0]; p += 4
        cls = b[p:p + n].decode(); p += n + (-n % 4)
        if not cls: return ('OBJ', None), p
        cnt = struct.unpack_from('<I', b, p)[0]; p += 4
        props = {}
        for _ in range(cnt):
            k, p = dec_str(b, p)
            v, p = dec(b, p)
            props[k] = v
        return ('OBJ', cls, props), p
    if t == 18:
        n = struct.unpack_from('<I', b, p)[0] & 0x7FFFFFFF; p += 4
        d = {}
        for _ in range(n):
            k, p = dec(b, p); v, p = dec(b, p)
            d[k if not isinstance(k, tuple) else repr(k)] = v
        return d, p
    if t == 19:
        n = struct.unpack_from('<I', b, p)[0] & 0x7FFFFFFF; p += 4
        a = []
        for _ in range(n):
            v, p = dec(b, p); a.append(v)
        return a, p
    if t == 20:
        n = struct.unpack_from('<I', b, p)[0]; p += 4
        v = Bytes(b[p:p + n]); p += n + (-n % 4)
        return v, p
    if t in (21, 22):
        n = struct.unpack_from('<I', b, p)[0]; p += 4
        v = list(struct.unpack_from('<%d%s' % (n, 'i' if t == 21 else 'f'), b, p)); p += 4 * n
        return (T[t], v), p
    if t == 23:
        n = struct.unpack_from('<I', b, p)[0]; p += 4
        a = []
        for _ in range(n):
            l = struct.unpack_from('<I', b, p)[0]; p += 4
            a.append(b[p:p + l].decode('utf-8', 'replace').rstrip('\0')); p += l + (-l % 4)
        return ('POOL_STRING', a), p
    if t in (24, 25, 26):
        k = {24: 2, 25: 3, 26: 4}[t]
        n = struct.unpack_from('<I', b, p)[0]; p += 4
        v = struct.unpack_from('<%df' % (n * k), b, p); p += 4 * n * k
        return (T[t], [v[i:i + k] for i in range(0, len(v), k)]), p
    raise ValueError('type %d (%s) at %d' % (t, T[t] if t < len(T) else '?', p - 4))

def dec_str(b, p):
    n = struct.unpack_from('<I', b, p)[0]; p += 4
    s = b[p:p + n].decode(); p += n + (-n % 4)
    return s, p

def load(raw):
    n = struct.unpack_from('<I', raw, 0)[0]
    v, p = dec(raw, 4)
    assert p == 4 + n, (p, n)
    return v

def schema(v, depth=0, maxlist=3, maxdepth=6):
    ind = '  ' * depth
    out = []
    if isinstance(v, dict):
        for k, x in v.items():
            if isinstance(x, (dict, list)) or (isinstance(x, tuple) and x and str(x[0]).startswith('POOL')) or isinstance(x, Bytes):
                out.append('%s%s: %s' % (ind, k, summ(x)))
                if depth < maxdepth and isinstance(x, (dict, list)):
                    out += schema(x, depth + 1, maxlist, maxdepth)
            else:
                out.append('%s%s = %s' % (ind, k, short(x)))
    elif isinstance(v, list):
        for i, x in enumerate(v[:maxlist]):
            out.append('%s[%d] %s' % (ind, i, summ(x) if isinstance(x, (dict, list)) else short(x)))
            if isinstance(x, (dict, list)) and depth < maxdepth:
                out += schema(x, depth + 1, maxlist, maxdepth)
        if len(v) > maxlist:
            out.append('%s... (%d items total)' % (ind, len(v)))
    return out

def summ(x):
    if isinstance(x, dict): return 'dict(%d)' % len(x)
    if isinstance(x, list): return 'list(%d)' % len(x)
    if isinstance(x, Bytes): return 'bytes(%d) head=%s' % (len(x), x[:16].hex())
    if isinstance(x, tuple): return '%s(%d) head=%s' % (x[0], len(x[1]), str(x[1][:3])[:120])
    return short(x)

def short(x):
    s = repr(x)
    return s if len(s) < 160 else s[:160] + '...'

if __name__ == '__main__':
    raw = open(sys.argv[1], 'rb').read()
    v = load(raw)
    print('\n'.join(schema(v, maxlist=int(sys.argv[2]) if len(sys.argv) > 2 else 3)))
