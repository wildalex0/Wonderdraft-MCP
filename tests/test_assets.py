import os
from pathlib import Path

import pytest
from PIL import Image

from wdmcp.assets.catalog import AssetNotFound, Catalog
from wdmcp.assets.pck import PckReader
from wdmcp.assets.themes import apply_theme
from wdmcp.codec.types import Color
from wdmcp.model.document import MapDocument

PCK = os.environ.get("WDMCP_PCK", r"C:\Program Files\Wonderdraft\Wonderdraft.pck")


def _need_pck():
    if not Path(PCK).is_file():
        pytest.skip("Wonderdraft.pck not present")


@pytest.fixture(scope="module")
def cat():
    """Built-in assets only (user dir pointed at nothing) for determinism."""
    _need_pck()
    return Catalog.load(PCK, Path("nonexistent-user-dir"))


@pytest.fixture(scope="module")
def full_cat():
    _need_pck()
    return Catalog.load(PCK)


def test_pck_reader_counts():
    _need_pck()
    r = PckReader(PCK)
    assert len(r.paths()) == 2404
    assert r.read("res://themes/Pastel.wonderdraft_theme").startswith(b"{")


def test_pck_lists_themes(cat):
    assert {"Adventure", "Pastel", "Terra"} <= set(cat.themes())
    assert len(cat.themes()) == 9


def test_name_lists(cat):
    n = cat.name_lists()
    assert {"settlements", "bodies_of_water"} <= set(n)
    assert "%sbrook" in n["settlements"]


def test_builtin_set_metadata(cat):
    s = cat.get_set("res://sprites/mountains/penned_mountains_large")
    assert (s.pack, s.category, s.name) == (None, "mountains", "Penned Mountains [Large]")
    assert (s.radius, s.offset, s.draw_mode) == (32.0, (0.0, -14.0), "sample_color")
    assert s.variants == sorted(s.variants)
    assert "res://sprites/mountains/penned_mountains_large/mountain_01" in s.variants
    assert any(x.pack == "Arabia by Chan" for x in cat.sets())


def test_sets_include_builtin_and_user(full_cat):
    keys = [s.key for s in full_cat.sets("mountains")]
    assert any(k.startswith("res://sprites/mountains/") for k in keys)
    if not any("Moulk's AI Fantasy Cartography Megapack" in s.key for s in full_cat.sets()):
        pytest.skip("user packs not installed")
    assert any("Moulk's AI Fantasy Cartography Megapack" in k for k in keys)


def test_user_pack_synthetic(tmp_path):
    d = tmp_path / "P" / "sprites" / "symbols" / "Set A"
    d.mkdir(parents=True)
    Image.new("RGBA", (4, 4)).save(d / "1.png")
    (d / ".wonderdraft_symbols").write_text("{not json")
    d2 = tmp_path / "P" / "sprites" / "trees" / "T"
    d2.mkdir(parents=True)
    Image.new("RGBA", (4, 4)).save(d2 / "a.png")
    (d2 / ".wonderdraft_symbols").write_text('{"name":"Tt","radius":7,"extra":1}')
    c = Catalog.load(tmp_path / "none.pck", tmp_path)
    s = c.get_set("user://assets/P/sprites/symbols/Set A")
    assert (s.radius, s.draw_mode, s.offset, s.name) == (50.0, "normal", (0.0, 0.0), "Set A")
    assert s.variants == ["user://assets/P/sprites/symbols/Set A/1"]
    assert c.image(s.variants[0]).size == (4, 4)
    assert c.get_set("user://assets/P/sprites/trees/T").radius == 7.0
    with pytest.raises(AssetNotFound):
        c.image("user://assets/P/sprites/symbols/Set A/nope")
    with pytest.raises(AssetNotFound):
        c.image("user://assets/../../etc/x")


def test_env_overrides(tmp_path, monkeypatch):
    d = tmp_path / "P" / "sprites" / "symbols" / "S"
    d.mkdir(parents=True)
    Image.new("RGBA", (2, 2)).save(d / "1.png")
    monkeypatch.setenv("WDMCP_PCK", str(tmp_path / "none.pck"))
    monkeypatch.setenv("WDMCP_USER_DIR", str(tmp_path))
    c = Catalog.load()
    assert [s.key for s in c.sets()] == ["user://assets/P/sprites/symbols/S"]


def test_missing_user_dir_is_ok(tmp_path):
    c = Catalog.load(tmp_path / "none.pck", tmp_path / "nodir")
    assert c.sets() == []


def test_image_loads_png(cat):
    s = cat.sets("symbols")[0]
    assert cat.image(s.variants[0]).mode in ("RGBA", "LA", "P", "RGB")
    with pytest.raises(AssetNotFound):
        cat.image("res://sprites/nope/x")


def test_unknown_set_suggests(cat):
    with pytest.raises(AssetNotFound) as e:
        cat.get_set("penned_mountians_large")
    assert e.value.key == "penned_mountians_large"
    assert any("penned_mountains_large" in s for s in e.value.suggestions)
    assert len(e.value.suggestions) <= 5 and not isinstance(e.value, KeyError)


def test_unknown_theme_suggests(cat):
    with pytest.raises(AssetNotFound) as e:
        cat.get_theme("Pastle")
    assert "Pastel" in e.value.suggestions


def test_apply_theme_changes_label_presets(cat):
    doc = MapDocument.new(800, 600)
    import copy
    theme = cat.themes()["Pastel"]
    theme_copy = copy.deepcopy(theme)
    before_cg = copy.deepcopy(doc.root["theme"]["color_grading"])
    apply_theme(doc, theme)
    t = doc.root["theme"]
    assert set(t["label_presets"]) == {"Town", "City", "Water", "Terrain"}  # brief said Region; Pastel has Terrain
    assert isinstance(t["coastline_color"], Color)
    assert all(isinstance(c, Color) for c in t["ground_colors"])
    assert isinstance(t["label_presets"]["Town"]["font_color"], Color)
    assert isinstance(t["label_presets"]["Town"]["font_size"], float)
    assert doc.root["theme"]["color_grading"] == before_cg  # absent from theme JSON
    assert theme == theme_copy  # input not mutated


def test_apply_theme_names_match_colors(cat):
    doc = MapDocument.new(800, 600)
    theme = next(t for t in cat.themes().values() if "ground_color_names" not in t)
    apply_theme(doc, theme)
    t = doc.root["theme"]
    assert len(t["ground_color_names"]) == len(t["ground_colors"]) == len(theme["ground_colors"])
    assert len(t["water_color_names"]) == len(t["water_colors"])


def test_user_pack_dotted_set_names(tmp_path):
    for sname, files in {"St. Johns": ["a.png", "house.v2.png"], "Set 1.a": ["x.png"], "Set 1.b": ["x.png"]}.items():
        d = tmp_path / "P" / "sprites" / "symbols" / sname
        d.mkdir(parents=True)
        for f in files:
            Image.new("RGBA", (2, 2)).save(d / f)
    c = Catalog.load(tmp_path / "none.pck", tmp_path)
    sets = c.sets()
    keys = [s.key for s in sets]
    assert len(set(keys)) == 3
    assert "user://assets/P/sprites/symbols/St. Johns" in keys
    for s in sets:
        assert c.get_set(s.key) is s
        assert all(v.startswith(s.key + "/") for v in s.variants)
        assert c.image(s.variants[0]).size == (2, 2)
    assert "user://assets/P/sprites/symbols/St. Johns/house.v2" in c.get_set(
        "user://assets/P/sprites/symbols/St. Johns").variants
