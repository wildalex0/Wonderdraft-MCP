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

    def __init__(self, known=(TOWN + "/a",), images=None):
        self.known = set(known) | set(images or {})
        self.images = images or {}

    def image(self, texture):
        if texture not in self.known:
            raise AssetNotFound(texture)
        return self.images.get(texture) or Image.new("RGBA", (20, 20), (255, 0, 0, 255))

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
    from wdmcp.codec.types import Color
    doc = land_doc()
    th = doc.root["theme"]  # a theme that already contrasts: left unchanged
    th["water_colors"] = [Color(0.57, 0.80, 0.72, 1.0)]
    th["ground_colors"] = [Color(0.90, 0.82, 0.67, 1.0), Color(0.85, 0.71, 0.56, 1.0)]
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


def sym(tex, x, y, rot=0.0, mirror=False):
    from wdmcp.codec.types import Vector2
    return {"texture": tex, "position": Vector2(x, y), "scale": Vector2(1, 1),
            "offset": Vector2(0, 0), "rotation": rot, "mirror": mirror, "z_index": 0}


def two_tone(w, h):
    """Left half red, right half blue."""
    im = Image.new("RGBA", (w, h), (255, 0, 0, 255))
    im.paste((0, 0, 255, 255), (w // 2, 0, w, h))
    return im


def test_preview_land_water_contrast_worn():
    doc = land_doc()
    img = render_preview(doc, None, scale=1.0)
    # land is painted over x,y in [50,350]x[50,250]; sample just inside / outside the west coast
    land = px(img, 60, 150, 1.0)
    water = px(img, 40, 150, 1.0)
    assert np.abs(land - water).mean() >= 60


def test_preview_good_theme_unchanged():
    from wdmcp.render import preview as pv
    th = MapDocument.open(FIXTURE).root["theme"]
    w = pv._theme_first(th, "water_colors", pv._DEF_WATER)
    g = pv._theme_list(th, "ground_colors", pv._DEF_GROUND)
    w2, p2 = pv._ensure_contrast(w, g, pv._theme_color(th, "coastline_color", (0, 0, 0, 1)))
    assert np.allclose(w2, np.array(w[:3]) * 255)
    assert np.allclose(p2, np.array([c[:3] for c in g]) * 255)


def test_preview_cache_not_shared_between_catalogs():
    doc = MapDocument.new(100, 100)
    doc.add("symbol", sym(TOWN + "/a", 50, 50))
    a = FakeCat(known=())
    p = px(render_preview(doc, a, scale=1.0), 50, 50, 1.0)
    assert p[2] > 200 and p[0] > 200  # magenta placeholder
    del a
    import gc
    gc.collect()
    p = px(render_preview(doc, FakeCat(), scale=1.0), 50, 50, 1.0)
    assert p[0] > 200 and p[2] < 60  # the real (red) sprite now


def test_preview_sprite_cache_bounds(monkeypatch):
    from wdmcp.render import preview as pv
    monkeypatch.setattr(pv, "_SPRITE_MAX_SIDE", 64)
    doc = MapDocument.new(400, 400)
    doc.add("symbol", {**sym(TOWN + "/a", 200, 200), "scale": __import__("wdmcp.codec.types", fromlist=["x"]).Vector2(50, 50)})
    cat = FakeCat()
    img = render_preview(doc, cat, scale=1.0)
    c = pv._cache_for(cat)
    assert max(max(k[1], k[2]) for k in c.sized) <= 64
    monkeypatch.setattr(pv, "_SPRITE_CACHE_BYTES", 100)
    for i in range(5):
        doc.add("symbol", {**sym(TOWN + "/a", 20 + i, 20), "scale": __import__("wdmcp.codec.types", fromlist=["x"]).Vector2(1 + i, 1 + i)})
    render_preview(doc, cat, scale=1.0)
    assert len(c.sized) == 1 or c.bytes <= 100 + 64 * 64 * 4


def test_preview_rotation_mirror_and_order():
    cat = FakeCat(images={"t/lr": two_tone(40, 10), "t/red": Image.new("RGBA", (20, 20), (255, 0, 0, 255)),
                          "t/green": Image.new("RGBA", (20, 20), (0, 255, 0, 255))})
    # +90 deg is clockwise on screen: the red (left) half moves to the top
    doc = MapDocument.new(200, 200)
    doc.add("symbol", sym("t/lr", 100, 100, rot=np.pi / 2))
    img = render_preview(doc, cat, scale=1.0)
    top, bottom, side = px(img, 100, 88, 1.0), px(img, 100, 112, 1.0), px(img, 112, 100, 1.0)
    assert top[0] > 200 and top[2] < 60
    assert bottom[2] > 200 and bottom[0] < 60
    assert side[0] < 200 or side[1] > 100  # non-square: the sprite is now narrow
    # mirror swaps left and right
    doc = MapDocument.new(200, 200)
    doc.add("symbol", sym("t/lr", 100, 100, mirror=True))
    img = render_preview(doc, cat, scale=1.0)
    assert px(img, 88, 100, 1.0)[2] > 200 and px(img, 112, 100, 1.0)[0] > 200
    # same z: the symbol with the larger y is drawn on top, whatever the list order
    doc = MapDocument.new(200, 200)
    doc.add("symbol", sym("t/green", 100, 110))
    doc.add("symbol", sym("t/red", 100, 100))
    img = render_preview(doc, cat, scale=1.0)
    assert px(img, 100, 105, 1.0)[1] > 200  # overlap shows the lower (green) one


def _ink_box(img, base):
    """Bounding box (w, h) of pixels that differ clearly from the background image."""
    diff = np.abs(np.asarray(img, int) - np.asarray(base, int)).sum(axis=2) > 60
    ys, xs = np.nonzero(diff)
    return xs.max() - xs.min() + 1, ys.max() - ys.min() + 1


def test_preview_label_rotation_is_degrees():
    base_doc = MapDocument.new(300, 300)
    base = render_preview(base_doc, None, scale=1.0)
    boxes = {}
    for rot in (0.0, 90.0, 1.5708):
        doc = MapDocument.new(300, 300)
        place.add_label(doc, "HORIZONTAL", (150, 150), size=24, rotation=rot)
        boxes[rot] = _ink_box(render_preview(doc, None, scale=1.0), base)
    w0, h0 = boxes[0.0]
    w90, h90 = boxes[90.0]
    assert w0 > 2 * h0           # unrotated text is wide
    assert h90 > 2 * w90         # 90 (degrees) is vertical
    w1, h1 = boxes[1.5708]       # 1.57 degrees is still almost flat
    assert w1 > 2 * h1


def test_preview_tints_sample_color_variants_per_variant():
    from wdmcp.assets.catalog import VariantMeta
    from wdmcp.codec.types import Color

    class MetaCat(FakeCat):
        def variant_meta(self, texture):
            mode = "sample_color" if texture.endswith("/bw") else "normal"
            return VariantMeta("x", 20.0, (0.0, 0.0), mode)

    white = Image.new("RGBA", (20, 20), (255, 255, 255, 255))
    cat = MetaCat(images={"s/bw": white, "s/col": white})
    doc = MapDocument.new(200, 200)
    doc.add("symbol", {**sym("s/bw", 60, 100), "sample": Color(0.0, 1.0, 0.0, 1.0)})
    doc.add("symbol", {**sym("s/col", 140, 100), "sample": Color(0.0, 1.0, 0.0, 1.0)})
    img = render_preview(doc, cat, scale=1.0)
    bw, col = px(img, 60, 100, 1.0), px(img, 140, 100, 1.0)
    assert bw[1] > 200 and bw[0] < 60 and bw[2] < 60   # tinted green
    assert col.min() > 200                              # normal sprite stays white
