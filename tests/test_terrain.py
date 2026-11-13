import hashlib
import time

import numpy as np
import pytest

from wdmcp.model import raster_spec as rs
from wdmcp.model.document import MapDocument
from wdmcp.ops import noise, terrain

W, H = 400, 300
POLY = [(100, 80), (300, 80), (300, 220), (100, 220)]
LAKE = [(170, 130), (230, 130), (230, 170), (170, 170)]
FULL = [(0, 0), (W, 0), (W, H), (0, H)]


def blank():
    return MapDocument.new(W, H)


def digest(doc):
    h = hashlib.sha256()
    for n in ("mask", "ground", "water_tint"):
        h.update(doc.raster(n).tobytes())
    return h.hexdigest()


def test_paint_land_inside_is_land():
    doc = blank()
    n = terrain.paint_land(doc, POLY, seed=1)
    m = doc.raster("mask")
    assert n > 0
    assert rs.is_land(m[150, 200, 3])
    assert not rs.is_land(m[H - 1, W - 1, 3])
    assert not rs.is_land(m[0, 0, 3])


def test_paint_land_heights_rise_inward():
    doc = blank()
    terrain.paint_land(doc, POLY, roughness=0, seed=1)
    a = doc.raster("mask")[..., 3]
    assert a[150, 200] > a[150, 103] >= rs.SEA_LEVEL_ALPHA


def test_paint_land_add_never_lowers():
    doc = blank()
    m = doc.raster("mask")
    m[150, 200, 3] = 255
    terrain.paint_land(doc, POLY, seed=3)
    assert doc.raster("mask")[150, 200, 3] == 255


def test_roughness_zero_is_plain_polygon():
    doc = blank()
    terrain.paint_land(doc, POLY, roughness=0, seed=1)
    land = rs.is_land(doc.raster("mask")[..., 3])
    assert land[150, 200] and not land[75, 200] and not land[225, 200]
    assert abs(int(land.sum()) - 200 * 140) < 800


def test_roughness_changes_outline():
    a, b = blank(), blank()
    terrain.paint_land(a, POLY, roughness=0, seed=1)
    terrain.paint_land(b, POLY, roughness=1, seed=1)
    la, lb = rs.is_land(a.raster("mask")[..., 3]), rs.is_land(b.raster("mask")[..., 3])
    assert (la != lb).sum() > 200


def test_subtract_restores_water():
    doc = blank()
    terrain.paint_land(doc, POLY, roughness=0.1, seed=1)
    g = doc.raster("ground")
    g[150, 200] = (9, 9, 9, 255)
    m = doc.raster("mask")
    m[150, 200, 0] = 200
    big = [(80, 60), (320, 60), (320, 240), (80, 240)]
    n = terrain.paint_land(doc, big, mode="subtract", roughness=0, seed=1)
    m = doc.raster("mask")
    assert n > 0
    assert not rs.is_land(m[150, 200, 3]) and m[150, 200, 0] == 0
    assert tuple(g[150, 200]) == (0, 0, 0, 0)
    assert not rs.is_land(m[..., 3]).any()


def test_subtract_shallow_near_edge():
    doc = blank()
    terrain.paint_land(doc, POLY, roughness=0, seed=1)
    terrain.paint_land(doc, POLY, mode="subtract", roughness=0, seed=1)
    a = doc.raster("mask")[..., 3]
    assert a[150, 103] > a[150, 200]


def test_bad_mode():
    with pytest.raises(ValueError):
        terrain.paint_land(blank(), POLY, mode="nope")


def test_deterministic_seed():
    def run(seed):
        d = blank()
        terrain.paint_land(d, POLY, seed=seed)
        terrain.add_lake(d, LAKE, seed=seed)
        terrain.add_river(d, [(120, 100), (200, 190), (280, 200)], seed=seed)
        terrain.paint_ground(d, POLY, 0)
        return digest(d)

    assert run(5) == run(5)
    assert run(5) != run(6)


def test_river_follows_points():
    doc = blank()
    terrain.paint_land(doc, FULL, roughness=0)
    pts = [(40, 50), (150, 120), (250, 100), (360, 250)]
    width = 4.0
    n = terrain.add_river(doc, pts, width=width, seed=2)
    r = doc.raster("mask")[..., 0]
    assert n > 0 and r.max() > 200
    ys, xs = np.nonzero(r)
    for px, py in pts:
        d = np.hypot(xs - px, ys - py).min()
        assert d <= width, (px, py, d)
    m = doc.raster("mask")
    assert (m[..., 1:3] == 0).all()


def test_river_tapers_toward_source():
    doc = blank()
    terrain.paint_land(doc, FULL, roughness=0)
    terrain.add_river(doc, [(20, 150), (380, 150)], width=8, seed=1)
    r = doc.raster("mask")[..., 0].astype(int)
    assert r[:, 25].sum() < r[:, 370].sum()


def test_river_on_water_changes_nothing():
    doc = blank()
    assert terrain.add_river(doc, [(10, 10), (300, 200)]) == 0
    assert (doc.raster("mask") == 0).all()


def test_lake_is_freshwater_on_land():
    doc = blank()
    terrain.paint_land(doc, POLY, roughness=0)
    before_alpha = doc.raster("mask")[..., 3].copy()
    n = terrain.add_lake(doc, LAKE, seed=1)
    m = doc.raster("mask")
    assert n > 1000
    assert m[150, 200, 0] == 255
    assert m[20, 20, 0] == 0
    assert (m[..., 3] == before_alpha).all()


def test_degenerate_and_out_of_map_inputs():
    doc = blank()
    assert terrain.paint_land(doc, []) == 0
    assert terrain.paint_land(doc, [(1, 1), (2, 2)]) == 0
    assert terrain.paint_land(doc, [(-500, -500), (-400, -500), (-400, -400)]) == 0
    terrain.paint_land(doc, [(-50, -50), (1e9, -50), (1e9, 1e9), (-50, 1e9)], seed=1)
    assert terrain.add_river(doc, [(-100, -100), (-50, -20)]) >= 0
    assert terrain.add_river(doc, [(5, 5)]) == 0
    assert terrain.add_lake(doc, [(float("nan"), 0), (1, 1), (5, 0)]) >= 0
    assert terrain.raise_area(doc, [(-9, -9), (5, -9), (5, 5)], 0.5) >= 0
    assert terrain.paint_ground(doc, [(-9, -9), (5, -9), (5, 5)], 0) >= 0


def test_raise_area_land_only():
    doc = blank()
    terrain.paint_land(doc, [(100, 80), (200, 80), (200, 220), (100, 220)], roughness=0)
    before = doc.raster("mask")[..., 3].copy()
    n = terrain.raise_area(doc, POLY, 0.3)
    a = doc.raster("mask")[..., 3]
    assert n > 0
    assert (a[before < 128] == before[before < 128]).all()
    land = before >= 128
    assert (a[land] >= before[land]).all() and (a[land] > before[land]).any()
    terrain.raise_area(doc, POLY, 5)
    assert doc.raster("mask")[150, 150, 3] == 255


def test_paint_ground():
    doc = blank()
    col = doc.root["theme"]["ground_colors"][1]
    n = terrain.paint_ground(doc, POLY, 1, softness=0.5)
    g = doc.raster("ground")
    assert n > 0
    assert tuple(g[150, 200]) == (round(col.r * 255), round(col.g * 255), round(col.b * 255), 255)
    assert 0 < g[80, 200, 3] < 255 or 0 < g[78, 200, 3] < 255 or 0 < g[82, 200, 3] < 255
    assert tuple(g[0, 0]) == (0, 0, 0, 0)
    assert tuple(doc.raster("mask")[150, 200]) == (0, 0, 0, 0)


def test_paint_ground_bad_index():
    with pytest.raises(IndexError, match="color_index"):
        terrain.paint_ground(blank(), POLY, 99)
    with pytest.raises(IndexError):
        terrain.paint_ground(blank(), POLY, -1)


@pytest.mark.parametrize("kind", ["continent", "archipelago", "island"])
@pytest.mark.parametrize("cov", [0.2, 0.45])
def test_generate_landmass_coverage(kind, cov):
    doc = blank()
    n = terrain.generate_landmass(doc, cov, seed=4, kind=kind)
    land = rs.is_land(doc.raster("mask")[..., 3])
    assert abs(land.mean() - cov) <= 0.1
    assert n > 0
    assert (doc.raster("mask")[..., :3] == 0).all()


def _components(b):
    seen = np.zeros_like(b)
    count = 0
    hh, ww = b.shape
    for y, x in zip(*np.nonzero(b)):
        if seen[y, x]:
            continue
        count += 1
        stack = [(y, x)]
        seen[y, x] = True
        while stack:
            cy, cx = stack.pop()
            for ny, nx in ((cy + 1, cx), (cy - 1, cx), (cy, cx + 1), (cy, cx - 1)):
                if 0 <= ny < hh and 0 <= nx < ww and b[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    stack.append((ny, nx))
    return count


def test_generate_landmass_island_central_and_archipelago_many():
    doc = blank()
    terrain.generate_landmass(doc, 0.2, seed=2, kind="island")
    land = rs.is_land(doc.raster("mask")[..., 3])
    assert land[H // 2, W // 2] and not land[0, 0]
    assert land[:5].sum() == 0 and land[-5:].sum() == 0
    doc2 = blank()
    terrain.generate_landmass(doc2, 0.2, seed=2, kind="archipelago")
    assert _components(rs.is_land(doc2.raster("mask")[..., 3])) >= 3


def test_generate_landmass_bad_kind_and_extremes():
    with pytest.raises(ValueError):
        terrain.generate_landmass(blank(), 0.3, 1, "moon")
    d = blank()
    terrain.generate_landmass(d, 0.0, 1, "continent")
    assert not rs.is_land(d.raster("mask")[..., 3]).any()
    terrain.generate_landmass(d, 1.0, 1, "continent")
    assert rs.is_land(d.raster("mask")[..., 3]).all()


def test_fbm_properties():
    a = noise.fbm((120, 200), 40, 4, 7)
    assert a.shape == (120, 200) and a.dtype == np.float32
    assert 0 <= a.min() and a.max() <= 1 and a.std() > 0.05
    assert np.array_equal(a, noise.fbm((120, 200), 40, 4, 7))
    assert not np.array_equal(a, noise.fbm((120, 200), 40, 4, 8))


def test_fbm_fast_at_full_size():
    t = time.perf_counter()
    noise.fbm((1080, 1920), 200, 5, 1)
    assert time.perf_counter() - t < 1.0


def test_terrain_roundtrip_saves(tmp_path):
    doc = blank()
    terrain.paint_land(doc, POLY, seed=1)
    terrain.add_lake(doc, LAKE, seed=1)
    terrain.add_river(doc, [(120, 100), (200, 190), (280, 200)], seed=1)
    terrain.paint_ground(doc, POLY, 2)
    want = {n: doc.raster(n).copy() for n in ("mask", "ground", "water_tint")}
    out = tmp_path / "t.wonderdraft_map"
    doc.save(out)
    back = MapDocument.open(out)
    for n, arr in want.items():
        assert np.array_equal(back.raster(n), arr)


def _perimeter(p):
    c = np.vstack([p, p[:1]])
    return float(np.hypot(*np.diff(c, axis=0).T).sum())


def _boundary_len(land):
    water = ~land
    edge = np.zeros_like(land)
    edge[1:] |= water[:-1]
    edge[:-1] |= water[1:]
    edge[:, 1:] |= water[:, :-1]
    edge[:, :-1] |= water[:, 1:]
    return int((land & edge).sum())


def test_displaced_outline_is_fractal():
    pts = np.array(POLY, dtype=float)
    plain = terrain._displace(pts, 0.0, np.random.default_rng(1))
    assert np.array_equal(plain, pts)
    rough = terrain._displace(pts, 0.6, np.random.default_rng(1))
    assert _perimeter(rough) >= 1.5 * _perimeter(pts)
    wild = terrain._displace(pts, 1.0, np.random.default_rng(1))
    assert np.isfinite(wild).all() and np.abs(wild).max() < 3 * max(W, H)


def test_raster_coast_longer_with_roughness():
    a, b = blank(), blank()
    terrain.paint_land(a, POLY, roughness=0, seed=1)
    terrain.paint_land(b, POLY, roughness=0.6, seed=1)
    la, lb = (rs.is_land(d.raster("mask")[..., 3]) for d in (a, b))
    assert _boundary_len(lb) >= 1.3 * _boundary_len(la)  # pixel count under-reads sub-pixel jaggedness


def test_river_visibly_meanders():
    doc = MapDocument.new(600, 300)
    terrain.paint_land(doc, [(0, 0), (600, 0), (600, 300), (0, 300)], roughness=0)
    width = 3.0
    terrain.add_river(doc, [(100, 150), (500, 150)], width=width, seed=3)
    ys, xs = np.nonzero(doc.raster("mask")[..., 0])
    assert np.abs(ys - 150).max() > 2 * width


def test_ground_feather_fits_in_box():
    doc = blank()
    terrain.paint_ground(doc, POLY, 0, softness=1.0)  # r = 10, box pad 22
    g = doc.raster("ground")[..., 3]
    assert g[:, 100 - 22].max() == 0 and g[:, 300 + 21].max() == 0
    assert g[80 - 22, :].max() == 0 and g[220 + 21, :].max() == 0
    assert g[150, 100 - 10] > 0


# ------------------------------------------------------------------ river source taper
def _river_doc(length=1100, width=10.0, seed=0):
    doc = MapDocument.new(length + 100, 300)
    terrain.paint_land(doc, [(0, 0), (length + 100, 0), (length + 100, 300), (0, 300)],
                       roughness=0)
    terrain.add_river(doc, [(50, 150), (50 + length, 150)], width, seed)
    return doc


def _band(doc, x):
    """Pixels with freshwater >= 128 in column x (>= the river's width where it crosses)."""
    return int((doc.raster("mask")[:, x, rs.FRESHWATER_CHANNEL] >= 128).sum())


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_long_river_is_full_width_away_from_source(seed):
    doc = _river_doc(1100, 10.0, seed)
    assert _band(doc, 50 + 300) >= 0.6 * 10
    # the ramp is capped at 6 widths, so 80 px from the source the river is nearly full width
    assert _band(doc, 50 + 80) >= 0.8 * 10


def test_thin_river_keeps_at_least_one_pixel_near_source():
    doc = _river_doc(600, 2.0, 0)
    assert _band(doc, 52) >= 1
