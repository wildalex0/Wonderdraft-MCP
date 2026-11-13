from pathlib import Path

import numpy as np

from wdmcp.codec import load_map_bytes
from wdmcp.model import raster_spec as rs
from wdmcp.model.document import TEMPLATE_PATH, MapDocument


def test_blank_template_is_all_water():
    doc = MapDocument.new(1920, 1080)
    for name in ("mask", "ground", "water_tint"):
        arr = doc.raster(name)
        assert arr.shape == (1080, 1920, 4)
        assert (arr == np.array(rs.WATER_FILL, dtype=np.uint8)).all()
    root = load_map_bytes(TEMPLATE_PATH)
    assert TEMPLATE_PATH.name == "blank.wonderdraft_map"
    for name in ("mask", "ground", "water_tint"):
        d = root[name].props["data"]
        buf = np.frombuffer(d["data"], dtype=np.uint8)
        assert not buf.any()
    assert root["symbols"] == [] and root["labels"] == [] and root["paths"] == []
    assert root["territories"]["territories"] == []
    assert root["windroses"] == [] and root["boxes"] == []


def test_land_alpha():
    a = rs.land_alpha(np.array([-1.0, 0.0, 0.5, 1.0, 2.0]))
    assert a.dtype == np.uint8
    assert a[0] == a[1] == rs.SEA_LEVEL_ALPHA == 128
    assert a[3] == a[4] == 255
    assert np.all(np.diff(a.astype(int)) >= 0)
    assert rs.is_land(a).all()


def test_water_alpha():
    a = rs.water_alpha(np.array([0.0, 0.5, 1.0]))
    assert a.dtype == np.uint8
    assert a[0] == 127 and a[2] == 0
    assert np.all(np.diff(a.astype(int)) <= 0)
    assert not rs.is_land(a).any()


def test_is_land_threshold():
    r = rs.is_land(np.array([0, 102, 127, 128, 153, 255], dtype=np.uint8))
    assert r.tolist() == [False, False, False, True, True, True]
    assert (rs.ERASE_ALPHA, rs.PAINT_LAND_ALPHA, rs.RAISED_ALPHA) == (102, 153, 255)


PROBE = Path(__file__).parent / "fixtures" / "probe_gui.wonderdraft_map"
_TA = "user://assets/T.A's Mapmaking Kit/sprites/symbols/[T.A.] Houses/13"
_MOULK = ("user://assets/Moulk's AI Fantasy Cartography Megapack/sprites/symbols/"
          "Mushroom Houses/Mushroom 5")


def test_user_pack_texture_matches_probe():
    assert rs.USER_TEXTURE_PREFIX == "user://assets/"
    ta = rs.user_pack_texture("T.A's Mapmaking Kit", "sprites/symbols/[T.A.] Houses/13.png")
    mk = rs.user_pack_texture("Moulk's AI Fantasy Cartography Megapack",
                              "sprites/symbols/Mushroom Houses/Mushroom 5.png")
    assert (ta, mk) == (_TA, _MOULK)
    textures = {e["texture"] for e in load_map_bytes(PROBE)["symbols"]}
    assert _TA in textures and _MOULK in textures
    bs = chr(92)
    assert rs.user_pack_texture("p", "sprites" + bs + "symbols" + bs + "a.png") == "user://assets/p/sprites/symbols/a"


def test_probe_freshwater_only_on_land():
    root = load_map_bytes(PROBE)
    d = root["mask"].props["data"]
    m = np.frombuffer(d["data"], dtype=np.uint8).reshape(d["height"], d["width"], 4)
    wet = m[..., 0] > 0
    assert wet.sum() > 1000
    assert (m[..., 3][wet] >= 128).all()
    assert not m[..., 1].any() and not m[..., 2].any()


def test_paint_freshwater():
    mask = np.zeros((2, 3, 4), dtype=np.uint8)
    mask[0, :, 3] = [200, 100, 128]   # land, water, land
    mask[1, :, 3] = [255, 255, 0]     # land, land, water
    mask[1, 0, 0] = 200
    cov = np.array([[1.0, 1.0, 0.5], [0.5, 0.0, 1.0]])
    n = rs.paint_freshwater(mask, cov)
    assert mask[..., 0].tolist() == [[255, 0, 128], [200, 0, 0]]
    assert n == 2
    assert rs.paint_freshwater(mask, (cov * 255).astype(np.uint8)) == 0
    assert rs.FRESHWATER_CHANNEL == 0
