import itertools
from pathlib import Path

import numpy as np
import pytest

from wdmcp.assets.catalog import AssetNotFound, Catalog, SymbolSet
from wdmcp.codec.types import Color, Vector2
from wdmcp.model import raster_spec as rs
from wdmcp.model.document import MapDocument
from wdmcp.model.points import parse_points
from wdmcp.ops import place, terrain

FIXTURE = Path(__file__).parent / "fixtures" / "testmap.wonderdraft_map"
W, H = 400, 300
FULL = [(0, 0), (W, 0), (W, H), (0, H)]

TOWN = "res://sprites/symbols/town_x"
PEAK = "res://sprites/mountains/peaks_x"
PINE = "res://sprites/trees/pines_x"
HOUSES = "user://assets/Pack/sprites/symbols/Houses"
CASTLE = "res://sprites/symbols/inked_castles"


def fake_catalog():
    c = Catalog(None, None)
    def mk(key, cat, radius, offset=(0.0, -25.0)):
        variants = [f"{key}/v{i}" for i in range(3)]
        c._sets[key] = SymbolSet(key, None if key.startswith("res://") else "Pack", cat,
                                 key.rsplit("/", 1)[-1], radius, offset, "normal", variants)
    mk(TOWN, "symbols", 40.0)
    mk(CASTLE, "symbols", 50.0)
    mk(PEAK, "mountains", 30.0)
    mk(PINE, "trees", 20.0)
    mk(HOUSES, "symbols", 60.0, (0.0, 0.0))
    return c


@pytest.fixture
def cat():
    return fake_catalog()


def blank():
    return MapDocument.new(W, H)


def land_doc():
    doc = blank()
    terrain.paint_land(doc, [(50, 50), (350, 50), (350, 250), (50, 250)], roughness=0, seed=1)
    return doc


def pos(doc, i):
    p = doc.get(i)["position"]
    return p.x, p.y


def test_add_symbol_entry_shape(cat):
    fx = MapDocument.open(FIXTURE)
    doc = blank()
    i = place.add_symbol(doc, cat, TOWN, (100, 100), variant=1)
    e = doc.get(i)
    assert set(e) == set(fx.get("symbol:0"))
    assert e["texture"] == f"{TOWN}/v1"
    assert e["type"] == "symbol" and e["radius"] == 40.0
    assert e["offset"] == Vector2(0.0, -25.0)
    assert doc.get(place.add_symbol(doc, cat, PEAK, (10, 10)))["type"] == "mountain"
    assert doc.get(place.add_symbol(doc, cat, PINE, (20, 20)))["type"] == "tree"


def test_add_symbol_user_pack_base_scale(cat):
    doc = blank()
    e = doc.get(place.add_symbol(doc, cat, HOUSES, (100, 100), scale=2.0))
    assert e["scale"] == Vector2(0.5, 0.5)
    e = doc.get(place.add_symbol(doc, cat, TOWN, (100, 100), scale=2.0))
    assert e["scale"] == Vector2(2.0, 2.0)


def test_add_symbol_errors(cat):
    doc = blank()
    with pytest.raises(ValueError):
        place.add_symbol(doc, cat, TOWN, (W + 1, 5))
    with pytest.raises(ValueError):
        place.add_symbol(doc, cat, TOWN, (-1, 5))
    with pytest.raises(ValueError):
        place.add_symbol(doc, cat, TOWN, (5, 5), variant=9)
    with pytest.raises(AssetNotFound):
        place.add_symbol(doc, cat, "res://nope", (5, 5))
    assert doc.root["symbols"] == []


def test_symbols_sorted_by_y_and_ids_resolve(cat):
    doc = blank()
    place.add_symbol(doc, cat, TOWN, (10, 200))
    place.add_symbol(doc, cat, TOWN, (10, 50))
    ids, _ = place.scatter(doc, cat, PEAK, FULL, 3.0, land_only=False, seed=2)
    ys = [e["position"].y for e in doc.root["symbols"]]
    assert ys == sorted(ys)
    assert len(ids) > 5 and len(set(ids)) == len(ids)
    assert len(doc.root["symbols"]) == len(ids) + 2
    for i in ids:
        assert doc.get(i)["type"] == "mountain"
    # single add: returned id points at the new entry even though it sorted in the middle
    i = place.add_symbol(doc, cat, PINE, (77, 150))
    assert doc.get(i)["texture"].startswith(PINE) and pos(doc, i) == (77.0, 150.0)


def test_scatter_density_and_spacing(cat):
    doc = blank()
    ids, dropped = place.scatter(doc, cat, TOWN, FULL, 4.0, min_spacing=25, land_only=False, seed=5)
    assert dropped == 0 and len(ids) > 20
    pts = [pos(doc, i) for i in ids]
    for a, b in itertools.combinations(pts, 2):
        assert np.hypot(a[0] - b[0], a[1] - b[1]) >= 25 - 1e-9


def test_scatter_default_spacing_is_radius(cat):
    doc = blank()
    ids, _ = place.scatter(doc, cat, TOWN, FULL, 50.0, land_only=False, seed=5)
    pts = [pos(doc, i) for i in ids]
    d = min(np.hypot(a[0] - b[0], a[1] - b[1]) for a, b in itertools.combinations(pts, 2))
    assert d >= 40 * 0.9 - 1e-9


def test_scatter_deterministic(cat):
    out = []
    for _ in range(2):
        doc = blank()
        place.scatter(doc, cat, TOWN, FULL, 3.0, land_only=False, seed=9)
        out.append([(e["texture"], e["position"], e["mirror"]) for e in doc.root["symbols"]])
    assert out[0] == out[1] and out[0]


def test_scatter_land_only(cat):
    doc = land_doc()
    ids, dropped = place.scatter(doc, cat, PINE, FULL, 5.0, land_only=True, seed=3)
    assert ids and dropped > 0
    m = doc.raster("mask")[..., 3]
    for i in ids:
        x, y = pos(doc, i)
        assert rs.is_land(m[int(y), int(x)])
    doc2 = land_doc()
    ids2, dropped2 = place.scatter(doc2, cat, PINE, FULL, 5.0, land_only=False, seed=3)
    assert dropped2 == 0 and len(ids2) > len(ids)


def test_scatter_clips_out_of_bounds(cat):
    doc = blank()
    poly = [(W - 100, -100), (W + 200, -100), (W + 200, H + 100), (W - 100, H + 100)]
    ids, dropped = place.scatter(doc, cat, TOWN, poly, 5.0, min_spacing=20, land_only=False, seed=1)
    assert dropped > 0 and ids
    for i in ids:
        x, y = pos(doc, i)
        assert 0 <= x < W and 0 <= y < H


def test_mountain_range_and_forest(cat):
    doc = land_doc()
    ids, dropped = place.mountain_range(doc, cat, PEAK, [(60, 150), (200, 100), (340, 160)],
                                        width=60, density=6.0, seed=4)
    assert isinstance(dropped, int) and len(ids) >= 5
    for i in ids:
        e = doc.get(i)
        assert e["type"] == "mountain"
        assert abs(e["position"].y - 150) < 90
    fids, fd = place.forest(doc, cat, PINE, [(60, 160), (340, 160), (340, 240), (60, 240)], 5.0, seed=4)
    assert fids and all(doc.get(i)["type"] == "tree" for i in fids)
    ys = [e["position"].y for e in doc.root["symbols"]]
    assert ys == sorted(ys)
    # off-map polyline: everything dropped or clipped, never out of bounds
    doc2 = blank()
    ids2, d2 = place.mountain_range(doc2, cat, PEAK, [(-300, -300), (-100, -100)], 50, 5.0)
    assert ids2 == [] and d2 > 0


def test_settlement_adds_symbol_and_label(cat):
    doc = blank()
    ids = place.add_settlement(doc, cat, "Oakvale", (200, 100), "city", set_key=TOWN)
    assert len(ids) == 2
    s, l = doc.get(ids[0]), doc.get(ids[1])
    assert l["text"] == "Oakvale"
    assert l["position"].x == 200.0
    # fake catalog has no image: radius fallback, offset and 0.35*size gap
    assert l["position"].y == pytest.approx(
        100 + s["offset"].y + s["radius"] * s["scale"].y + 0.35 * l["size"])
    assert l["size"] == 48  # City preset of the blank theme
    ids = place.add_settlement(doc, cat, "Hamlet", (100, 100), "village", set_key=HOUSES, label=False)
    assert len(ids) == 1


def test_settlement_default_sets(cat):
    assert place.default_settlement_set(cat, "town") == TOWN
    assert place.default_settlement_set(cat, "castle") == CASTLE
    assert place.default_settlement_set(cat, "mine") in (TOWN, CASTLE)  # fallback: any builtin
    cat._sets["res://sprites/symbols/gold_mines"] = SymbolSet(
        "res://sprites/symbols/gold_mines", None, "symbols", "Gold", 5.0,
        (0.0, 0.0), "normal", ["a"])
    cat._sets["res://sprites/symbols/comfort"] = SymbolSet(
        "res://sprites/symbols/comfort", None, "symbols", "Comfort", 5.0,
        (0.0, 0.0), "normal", ["a"])
    assert place.default_settlement_set(cat, "mine") == "res://sprites/symbols/gold_mines"
    assert place.default_settlement_set(cat, "fort") == CASTLE  # not "comfort"
    empty = Catalog(None, None)
    with pytest.raises(ValueError):
        place.default_settlement_set(empty, "town")
    doc = blank()
    ids = place.add_settlement(doc, cat, "X", (50, 50), "castle")
    assert doc.get(ids[0])["texture"].startswith(CASTLE)
    with pytest.raises(ValueError):
        place.add_settlement(doc, cat, "X", (-5, 50), "castle")


def test_path_points_string():
    doc = blank()
    i = place.add_path(doc, [(10, 20), (100.5, 50), (200, 120)], color=(1, 0, 0), seed=3)
    e = doc.get(i)
    assert e["points"] == "[ Vector2( 10, 20 ), Vector2( 100.5, 50 ), Vector2( 200, 120 ) ]"
    assert e["style"] == "res://textures/paths/path_blended"
    assert e["color"] == Color(1.0, 0.0, 0.0, 1.0)
    assert e["straight"] is False and isinstance(e["noise_seed"], int)
    i2 = place.add_path(doc, [(0, 0), (5, 5)], style="res://textures/paths/other")
    assert doc.get(i2)["style"] == "res://textures/paths/other"
    assert doc.get(place.add_path(doc, [(1, 1), (9, 9)], seed=3))["noise_seed"] == e["noise_seed"]


def test_path_and_territory_clamp_to_map():
    doc = blank()
    p = parse_points(doc.get(place.add_path(doc, [(-50, 10), (W + 50, 500), (100, 100)]))["points"])
    assert p == [(0.0, 10.0), (float(W), float(H)), (100.0, 100.0)]
    t = doc.get(place.add_territory(doc, [(-10, -10), (W + 10, -10), (W + 10, H + 10)], (0, 0.5, 1)))
    assert all(0 <= x <= W and 0 <= y <= H for x, y in parse_points(t["points"]))
    with pytest.raises(ValueError):
        place.add_path(doc, [(1, 1)])
    with pytest.raises(ValueError):
        place.add_territory(doc, [(1, 1), (2, 2)], (1, 1, 1))


def test_territory_entry_shape():
    fx = MapDocument.open(FIXTURE)
    doc = blank()
    e = doc.get(place.add_territory(doc, [(10, 10), (100, 10), (100, 100)], Color(0.1, 0.2, 0.3, 1.0)))
    assert doc.root["territories"]["territories"][0] is e
    assert e["style"] == "res://textures/borders/border_dash"
    if fx.list("territory"):
        assert set(e) == set(fx.list("territory")[0][1])


def test_label_uses_preset_font():
    doc = blank()
    pr = doc.root["theme"]["label_presets"]["City"]
    e = doc.get(place.add_label(doc, "Hi", (50, 50), preset="City"))
    assert e["font"] == pr["font_name"] and e["size"] == int(round(pr["font_size"]))
    assert e["color"] == pr["font_color"] and e["outline_size"] == int(round(pr["font_outline_width"]))
    assert e["outline_color"] == pr["font_outline_color"]
    e = doc.get(place.add_label(doc, "Hi", (50, 50), size=12, color=(0, 1, 0), rotation=0.3, curve=0.1))
    assert e["size"] == 12 and e["color"] == Color(0.0, 1.0, 0.0, 1.0) and e["curve"] == 0.1
    with pytest.raises(ValueError):
        place.add_label(doc, "x", (W, 5))


def test_label_preset_fallback():
    doc = blank()  # blank theme: City, Town, Water
    assert place.label_preset_used(doc, "Region") == "Town"
    assert place.label_preset_used(doc, "City") == "City"
    presets = doc.root["theme"]["label_presets"]
    presets["Terrain"] = presets.pop("Town")
    assert place.label_preset_used(doc, "Region") == "Terrain"
    assert place.label_preset_used(doc, "Water") == "Water"
    del presets["Water"]
    assert place.label_preset_used(doc, "Water") == "City"  # Water, Town missing -> first available
    with pytest.raises(ValueError, match="Bogus"):
        place.label_preset_used(doc, "Bogus")
    presets.clear()
    with pytest.raises(ValueError):
        place.label_preset_used(doc, "City")


def test_roundtrip_save(tmp_path, cat):
    doc = land_doc()
    place.scatter(doc, cat, PINE, FULL, 2.0, seed=1)
    place.add_settlement(doc, cat, "A", (100, 100), "town")
    place.add_path(doc, [(10, 10), (50, 60)])
    place.add_territory(doc, [(10, 10), (80, 10), (80, 80)], (1, 0, 0))
    out = doc.save(tmp_path / "m.wonderdraft_map")
    back = MapDocument.open(out)
    assert len(back.root["symbols"]) == len(doc.root["symbols"])
    assert len(back.list("label")) == 1 and len(back.list("territory")) == 1


def test_settlement_label_below_real_sprite(cat):
    from PIL import Image
    cat.image = lambda tex: Image.new("RGBA", (100, 200))
    doc = blank()
    ids = place.add_settlement(doc, cat, "Hut", (200, 100), "village", set_key=HOUSES)
    s, l = doc.get(ids[0]), doc.get(ids[1])
    assert s["scale"].y == 0.25
    # 200px * 0.25 / 2 = 25 below the sprite centre, plus a gap
    assert l["position"].y >= 100 + s["offset"].y + 25 + 0.35 * l["size"] - 1e-9


def test_pack_of_texture():
    assert place.pack_of_texture("user://assets/My Pack/sprites/symbols/H/1") == ("user", "My Pack")
    assert place.pack_of_texture("res://packs/Arabia/sprites/symbols/x/1") == ("builtin", "Arabia")
    assert place.pack_of_texture("res://sprites/symbols/x/1") is None


def test_symbols_register_packs(cat, tmp_path):
    ARAB = "res://packs/Arabia/sprites/trees/palms"
    cat._sets[ARAB] = SymbolSet(ARAB, "Arabia", "trees", "palms", 10.0, (0.0, 0.0), "normal", [ARAB + "/p"])
    doc = blank()
    doc.root["included_packs"] = ["Other"]
    before = list(doc.root["included_default_packs"])
    place.add_symbol(doc, cat, TOWN, (10, 10))
    assert doc.root["included_packs"] == ["Other"]
    assert doc.root["included_default_packs"] == before
    place.add_symbol(doc, cat, HOUSES, (20, 20))
    place.add_symbol(doc, cat, HOUSES, (30, 30))
    place.scatter(doc, cat, ARAB, FULL, 1.0, land_only=False)
    assert doc.root["included_packs"] == ["Other", "Pack"]
    assert doc.root["included_default_packs"] == before + ["Arabia"]
    back = MapDocument.open(doc.save(tmp_path / "m.wonderdraft_map"))
    assert back.root["included_packs"] == ["Other", "Pack"]
    assert back.root["included_default_packs"] == before + ["Arabia"]


# ------------------------------------------------------------------ rotation units (GUI-pinned)
def test_symbol_rotation_degrees_stored_as_radians(cat):
    import math
    doc = blank()
    e = doc.get(place.add_symbol(doc, cat, TOWN, (100, 100), rotation=90))
    assert e["rotation"] == pytest.approx(math.pi / 2)


def test_label_rotation_degrees_stored_as_is():
    doc = blank()
    e = doc.get(place.add_label(doc, "Vertical", (100, 100), rotation=90))
    assert e["rotation"] == 90.0
