import time
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from wdmcp.assets.catalog import AssetNotFound, Catalog, DEFAULT_PCK
from wdmcp.model.document import MapDocument
from wdmcp.ops import place, terrain
from wdmcp.render.preview import render_preview

FIXTURE = Path(__file__).parent / "fixtures" / "testmap.wonderdraft_map"
TOWN = "res://sprites/symbols/town_x"


class FakeCat:
    """Tiny catalog: every known texture is a solid red 20x20 square."""

    def __init__(self, known=(TOWN + "/a",)):
        self.known = set(known)

    def image(self, texture):
        if texture not in self.known:
            raise AssetNotFound(texture)
        return Image.new("RGBA", (20, 20), (255, 0, 0, 255))

    def get_set(self, key):
        raise AssetNotFound(key)


def land_doc(w=400, h=300):
    doc = MapDocument.new(w, h)
    terrain.paint_land(doc, [(50, 50), (350, 50), (350, 250), (50, 250)], roughness=0, seed=1)
    return doc


def px(img, x, y, scale=0.5):
    return np.array(img.convert("RGB").getpixel((int(x * scale), int(y * scale))), float)


def test_preview_size():
    doc = MapDocument.open(FIXTURE)
    img = render_preview(doc, None, scale=0.5)
    assert img.size == (960, 540)


def test_preview_bbox_crop():
    doc = land_doc()
    img = render_preview(doc, None, scale=0.5, bbox=(40, 40, 140, 100))
    assert img.size == (50, 30)
    full = render_preview(doc, None, scale=1.0)
    crop = render_preview(doc, None, scale=1.0, bbox=(100, 100, 200, 200))
    assert crop.size == (100, 100)
    assert np.abs(np.asarray(crop, int) - np.asarray(full.crop((100, 100, 200, 200)), int)).max() <= 2


def test_preview_land_water_colours():
    doc = land_doc()
    img = render_preview(doc, None, scale=1.0)
    water = px(img, 10, 10, 1.0)
    land = px(img, 200, 150, 1.0)
    wc = doc.root["theme"]["water_colors"][0]
    assert np.abs(water - np.array([wc.r, wc.g, wc.b]) * 255).max() < 12
    assert np.abs(land - water).sum() > 40


def test_preview_freshwater_visible():
    doc = land_doc()
    terrain.add_river(doc, [(100, 150), (300, 150)], width=6)
    img = render_preview(doc, None, scale=1.0)
    r = doc.raster("mask")[..., 0]
    y, x = np.unravel_index(np.argmax(r), r.shape)
    assert r[y, x] > 200
    on = px(img, x, y, 1.0)
    off = px(img, 200, 60, 1.0)  # plain land, away from the river
    assert np.abs(on - off).sum() > 40


def test_preview_draws_symbol():
    doc = MapDocument.new(200, 200)
    doc.add("symbol", {"texture": TOWN + "/a", "position": __import__("wdmcp.codec.types", fromlist=["x"]).Vector2(100, 100),
                        "scale": __import__("wdmcp.codec.types", fromlist=["x"]).Vector2(1, 1),
                        "offset": __import__("wdmcp.codec.types", fromlist=["x"]).Vector2(0, 0),
                        "rotation": 0.0, "mirror": False, "z_index": 0})
    bg = render_preview(MapDocument.new(200, 200), None, scale=1.0)
    img = render_preview(doc, FakeCat(), scale=1.0)
    p = px(img, 100, 100, 1.0)
    assert p[0] > 200 and p[1] < 60  # the red sprite
    assert np.abs(p - px(bg, 100, 100, 1.0)).sum() > 100


def test_preview_unknown_texture_draws_placeholder():
    from wdmcp.codec.types import Vector2
    doc = MapDocument.new(200, 200)
    doc.add("symbol", {"texture": "res://sprites/symbols/nope/x", "position": Vector2(100, 100),
                        "scale": Vector2(0.25, 0.25), "offset": Vector2(0, 0),
                        "rotation": 0.0, "mirror": False, "z_index": 0})
    for cat in (FakeCat(), None):
        img = render_preview(doc, cat, scale=1.0)
        p = px(img, 100, 100, 1.0)
        assert p[0] > 200 and p[1] < 60 and p[2] > 200  # magenta


def test_preview_objects_and_ids_do_not_crash():
    doc = MapDocument.open(FIXTURE)
    a = render_preview(doc, None, scale=0.5)
    b = render_preview(doc, None, scale=0.5, show_ids=True)
    assert a.size == b.size
    assert np.asarray(a).tobytes() != np.asarray(b).tobytes()


def test_preview_does_not_modify_doc():
    doc = MapDocument.open(FIXTURE)
    before = doc.raster("mask").copy()
    render_preview(doc, None)
    assert np.array_equal(before, doc.raster("mask"))


def test_preview_fixture_time():
    doc = MapDocument.open(FIXTURE)
    t = time.perf_counter()
    render_preview(doc, None, scale=0.5)
    assert time.perf_counter() - t < 5


@pytest.mark.skipif(not Path(DEFAULT_PCK).is_file(), reason="Wonderdraft.pck absent")
def test_preview_fixture_real_catalog_time():
    doc = MapDocument.open(FIXTURE)
    cat = Catalog.load()
    render_preview(doc, cat, scale=0.5)  # warm
    t = time.perf_counter()
    img = render_preview(doc, cat, scale=0.5)
    assert img.size == (960, 540)
    assert time.perf_counter() - t < 5
