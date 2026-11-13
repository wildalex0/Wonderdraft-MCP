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


def test_user_pack_texture_provisional():
    assert (rs.user_pack_texture("mypack", "sprites/symbols/set/tree.png")
            == "res://packs/mypack/sprites/symbols/set/tree")
    assert rs.user_pack_texture("p", "sprites\\symbols\\s\\a.png") == "res://packs/p/sprites/symbols/s/a"
