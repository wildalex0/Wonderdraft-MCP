import math
import shutil
from pathlib import Path

import numpy as np
import pytest
from PIL import Image as PILImage

from mcp.server.mcpserver import Image
from mcp.server.mcpserver.exceptions import ToolError

from wdmcp import server
from wdmcp.assets.catalog import Catalog, DEFAULT_PCK, SymbolSet
from wdmcp.codec.types import Color, Vector2
from wdmcp.model.document import MapDocument
from wdmcp.model.points import parse_points

FIXTURE = Path(__file__).parent / "fixtures" / "testmap.wonderdraft_map"
TOWN = "res://sprites/symbols/town_x"
PEAK = "res://sprites/mountains/peaks_x"
LAND = [[50, 50], [350, 50], [350, 250], [50, 250]]


def fake_catalog():
    c = Catalog(None, None)
    for key, cat, radius in ((TOWN, "symbols", 40.0), (PEAK, "mountains", 30.0)):
        c._sets[key] = SymbolSet(key, None, cat, key.rsplit("/", 1)[-1], radius, (0.0, -25.0),
                                 "normal", [f"{key}/v{i}" for i in range(3)])
    c._themes = {"Adventure": {"water_hue": 0.5}, "Paper": {"water_hue": 0.1}}
    return c


@pytest.fixture(autouse=True)
def session():
    s = server.reset_session(fake_catalog())
    yield s
    server.reset_session()


def sample_map():
    server.new_map(400, 300)
    server.paint_land(LAND, roughness=0, seed=1)


# ------------------------------------------------------------------ errors
def test_no_map_open_error():
    with pytest.raises(ToolError, match="No map is open. Call new_map or open_map first."):
        server.add_label("Nowhere", [10, 10])
    with pytest.raises(ToolError, match="No map is open"):
        server.render_preview()
    with pytest.raises(ToolError, match="No map is open"):
        server.map_summary()


def test_unknown_theme_lists_matches():
    with pytest.raises(ToolError, match="Adventure"):
        server.new_map(200, 100, theme="Adventur")
    assert server.session().current is None


def test_unknown_asset_suggests():
    sample_map()
    with pytest.raises(ToolError, match="town_x"):
        server.add_symbol("res://sprites/symbols/town_y", [100, 100])


def test_out_of_bounds_symbol_and_bad_id():
    sample_map()
    with pytest.raises(ToolError, match="outside the map"):
        server.add_symbol(TOWN, [-5, 10])
    with pytest.raises(ToolError):
        server.remove_objects(["symbol:99"])
    with pytest.raises(ToolError, match="malformed"):
        server.update_object("nope", {})


def test_failed_tool_rolls_back_and_is_not_undoable(session):
    sample_map()
    depth = len(session.history)
    with pytest.raises(ToolError):
        server.add_settlement("X", [100, 100], "city", set_key="res://nope")
    assert len(session.history) == depth
    assert server.list_objects("symbol")["count"] == 0


# ------------------------------------------------------------------ flow
def test_tool_flow(tmp_path):
    r = server.new_map(400, 300)
    assert r["ids"] == [] and "400x300" in r["summary"]
    r = server.generate_landmass(coverage=0.4, seed=3, kind="continent")
    assert r["pixels_changed"] > 0
    land = server.map_summary()["land_fraction"]
    assert 0.3 < land < 0.5
    r = server.paint_land(LAND, roughness=0, seed=1)
    r = server.add_settlement("Riverton", [200, 150], "town", set_key=TOWN)
    assert len(r["ids"]) == 2
    sym_id, label_id = r["ids"]
    assert sym_id.startswith("symbol:") and label_id.startswith("label:")
    assert "shift" in r["summary"]
    prev = server.render_preview()
    assert isinstance(prev[0], Image)
    path = tmp_path / "flow.wonderdraft_map"
    sv = server.save_map(str(path))
    assert Path(sv["path"]) == path and path.exists()
    assert server.map_summary()["dirty"] is False

    server.open_map(str(path))
    syms = server.list_objects("symbol")["objects"]
    labels = server.list_objects("label")["objects"]
    assert [s["id"] for s in syms] == [sym_id]
    assert [l["id"] for l in labels] == [label_id]
    assert labels[0]["text"] == "Riverton"
    s = server.map_summary()
    assert s["counts"] == {"symbol": 1, "label": 1, "path": 0, "territory": 0}
    assert s["path"] == str(path)
    assert abs(s["land_fraction"] - land) < 0.5  # same map, reloaded


def test_symbol_ids_valid_after_call():
    sample_map()
    a = server.add_symbol(TOWN, [100, 200])["ids"][0]
    b = server.add_symbol(TOWN, [100, 100])["ids"][0]  # sorts before a
    assert b == "symbol:0"
    rows = {r["id"]: r for r in server.list_objects("symbol")["objects"]}
    assert rows[b]["position"] == [100.0, 100.0]
    r = server.scatter_symbols(TOWN, [[60, 60], [340, 60], [340, 240], [60, 240]],
                               density=0.5, seed=2)
    assert r["ids"] and "dropped" in r
    rows = {r["id"]: r for r in server.list_objects("symbol")["objects"]}
    assert all(i in rows for i in r["ids"])


def test_placement_tools():
    sample_map()
    assert server.add_mountain_range(PEAK, [[80, 150], [320, 150]], 40, 0.5)["ids"]
    assert server.add_forest(TOWN, [[60, 60], [200, 60], [200, 120]], 0.3)["ids"]
    r = server.add_label("Big Bay", [200, 40], preset="Town")
    assert r["ids"] == ["label:0"] and "preset_used" in r
    r = server.add_path([[60, 60], [300, 200]], width=3)
    assert r["ids"] == ["path:0"]
    r = server.add_territory([[60, 60], [300, 60], [300, 200]], color="#ff0000")
    assert r["ids"] == ["territory:0"]
    e = server.session().current.get("territory:0")
    assert e["color"] == Color(1.0, 0.0, 0.0, 1.0)


def test_terrain_tools():
    sample_map()
    assert server.add_lake([[150, 100], [220, 100], [220, 160], [150, 160]])["pixels_changed"] > 0
    assert server.add_river([[60, 60], [120, 200]], width=4)["pixels_changed"] > 0
    assert server.raise_area([[100, 100], [200, 100], [200, 200]], 0.3)["pixels_changed"] >= 0
    assert server.paint_ground([[100, 100], [200, 100], [200, 200]], 0)["pixels_changed"] > 0
    assert server.paint_land([[60, 60], [100, 60], [100, 100]], mode="subtract")["ids"] == []


# ------------------------------------------------------------------ undo
def test_undo(session):
    with pytest.raises(ToolError, match="No map is open"):
        server.undo()
    server.new_map(400, 300)
    with pytest.raises(ToolError, match="Nothing to undo"):
        server.undo()
    doc = session.current
    before = doc.raster("mask").copy()
    server.paint_land(LAND, roughness=0, seed=1)
    assert not np.array_equal(doc.raster("mask"), before)
    server.undo()
    assert np.array_equal(session.current.raster("mask"), before)

    server.paint_land(LAND, roughness=0, seed=1)
    painted = session.current.raster("mask").copy()
    server.add_settlement("A", [200, 150], "town", set_key=TOWN)
    server.update_object("label:0", {"text": "B"})
    assert session.current.get("label:0")["text"] == "B"
    server.undo()
    assert session.current.get("label:0")["text"] == "A"
    server.undo()
    assert server.list_objects("symbol")["count"] == 0
    assert server.list_objects("label")["count"] == 0
    assert np.array_equal(session.current.raster("mask"), painted)  # raster untouched by objects
    assert session.current.root["included_packs"] == []


def test_undo_depth_is_bounded(session):
    sample_map()
    for i in range(25):
        server.add_label(f"L{i}", [10 + i, 10])
    assert len(session.history) == 20
    for _ in range(20):
        server.undo()
    with pytest.raises(ToolError):
        server.undo()
    assert server.list_objects("label")["count"] == 5  # L0..L4 predate the 20-deep window


def test_undo_memory_budget(session):
    sample_map()
    session.history_budget = 1  # absurdly small: oldest snapshots are evicted, newest is kept
    server.paint_land([[60, 60], [100, 60], [100, 100]], mode="subtract")
    server.paint_land(LAND, roughness=0, seed=2)
    assert len(session.history) == 1
    server.undo()
    with pytest.raises(ToolError, match="Nothing to undo"):
        server.undo()


def test_undo_after_new_map_restores_previous_map(session):
    sample_map()
    server.add_label("Keep", [10, 10])
    prev = session.current
    r = server.new_map(100, 100)
    assert r["replaced_unsaved_map"] is True and r["warnings"]
    assert "undo restores it" in r["summary"]
    server.undo()
    assert session.current is prev and session.dirty
    assert len(prev.list("label")) == 1
    server.undo()  # the previous map's own history is back too
    assert prev.list("label") == []


def test_new_map_from_nothing_has_no_undo(session):
    r = server.new_map(100, 100)
    assert r["replaced_unsaved_map"] is False and r["warnings"] == []
    with pytest.raises(ToolError, match="Nothing to undo"):
        server.undo()


def test_open_map_keeps_previous_for_undo(session, tmp_path):
    from pathlib import Path
    fx = Path(__file__).parent / "fixtures" / "testmap.wonderdraft_map"
    sample_map()
    prev = session.current
    r = server.open_map(str(fx))
    assert r["replaced_unsaved_map"] is True and Path(r["path"]).is_absolute()
    assert not session.dirty
    server.undo()
    assert session.current is prev
    # opening over a saved, clean map does not warn
    server.save_map(str(tmp_path / "s.wonderdraft_map"))
    r = server.open_map(str(fx))
    assert r["replaced_unsaved_map"] is False and r["warnings"] == []


# ------------------------------------------------------------------ editing
def test_update_object_conversion():
    sample_map()
    server.add_settlement("Town", [200, 150], "town", set_key=TOWN)
    server.update_object("symbol:0", {"position": [10, 20], "scale": [2, 2],
                                      "offset": [1, 2], "outline_color": "#00ff00",
                                      "sample": [0.1, 0.2, 0.3], "rotation": 90})
    e = server.session().current.get("symbol:0")
    assert e["position"] == Vector2(10.0, 20.0)
    assert e["scale"] == Vector2(2.0, 2.0) and e["offset"] == Vector2(1.0, 2.0)
    assert e["outline_color"] == Color(0.0, 1.0, 0.0, 1.0)
    assert e["sample"] == Color(0.1, 0.2, 0.3, 1.0)
    assert e["rotation"] == pytest.approx(math.pi / 2)  # degrees in, radians stored
    server.update_object("label:0", {"color": [1, 0, 0, 0.5], "text": "Renamed", "rotation": 90})
    lab = server.session().current.get("label:0")
    assert lab["color"] == Color(1.0, 0.0, 0.0, 0.5) and lab["text"] == "Renamed"
    assert lab["rotation"] == 90.0  # labels store degrees
    server.add_path([[10, 10], [50, 50]])
    server.update_object("path:0", {"points": [[1, 2], [3.5, 4]], "color": "#0000ff"})
    p = server.session().current.get("path:0")
    assert parse_points(p["points"]) == [(1.0, 2.0), (3.5, 4.0)]
    assert p["color"] == Color(0.0, 0.0, 1.0, 1.0)


def test_update_object_rejects_unknown_keys():
    sample_map()
    server.add_label("x", [10, 10])
    with pytest.raises(ToolError) as ei:
        server.update_object("label:0", {"bogus": 1})
    assert "bogus" in str(ei.value) and "text" in str(ei.value)  # lists valid keys


def test_move_objects_path_and_symbol():
    sample_map()
    server.add_path([[10, 10], [50, 60]])
    server.add_territory([[10, 10], [50, 10], [50, 60]], color=[1, 0, 0])
    server.add_label("L", [100, 100])
    doc = server.session().current
    r = server.move_objects(["path:0", "territory:0", "label:0"], 5, -2.5)
    assert r["ids"] == ["path:0", "territory:0", "label:0"]
    assert parse_points(doc.get("path:0")["points"]) == [(15.0, 7.5), (55.0, 57.5)]
    assert parse_points(doc.get("territory:0")["points"]) == [(15, 7.5), (55, 7.5), (55, 57.5)]
    assert doc.get("label:0")["position"] == Vector2(105.0, 97.5)


def test_remove_objects_and_list_bbox():
    sample_map()
    server.add_label("a", [10, 10])
    server.add_label("b", [300, 200])
    assert [o["text"] for o in server.list_objects("label", bbox=[0, 0, 100, 100])["objects"]] == ["a"]
    r = server.remove_objects(["label:0"])
    assert r["ids"] == [] and "1" in r["summary"]
    assert server.list_objects("label")["objects"][0]["text"] == "b"
    with pytest.raises(ToolError):
        server.list_objects("nonsense")


def test_set_frame_and_apply_theme():
    sample_map()
    r = server.set_frame(True, texture="celtic_dotted", size=1.5)
    f = server.session().current.root["frame"]
    assert f["enabled"] is True and f["texture"] == "celtic_dotted" and f["size"] == 1.5
    assert "ids" in r
    server.set_frame(False)
    assert server.session().current.root["frame"]["enabled"] is False
    assert server.session().current.root["frame"]["texture"] == "celtic_dotted"
    server.apply_theme("Paper")
    assert server.session().current.root["theme"]["water_hue"] == pytest.approx(0.1)
    with pytest.raises(ToolError, match="Paper"):
        server.apply_theme("Papr")
    assert server.list_themes()["themes"] == ["Adventure", "Paper"]


def test_list_asset_sets():
    r = server.list_asset_sets(category="mountains")
    assert r["total"] == 1
    row = r["sets"][0]
    assert row == {"key": PEAK, "name": "peaks_x", "category": "mountains",
                   "pack": None, "variants": 3}
    assert server.list_asset_sets(query="town")["total"] == 1
    assert len(server.list_asset_sets(limit=1)["sets"]) == 1
    with pytest.raises(ToolError, match="category"):
        server.list_asset_sets(category="bogus")


# ------------------------------------------------------------------ open/save/preview
def test_open_map_leaves_input_identical(tmp_path):
    src = tmp_path / "in.wonderdraft_map"
    shutil.copy(FIXTURE, src)
    before = src.read_bytes()
    r = server.open_map(str(src))
    assert "symbol" in r["summary"] or "objects" in r["summary"]
    server.move_objects(["symbol:0"], 1, 1) if server.list_objects("symbol")["count"] else None
    server.render_preview()
    assert src.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["in.preview.png", "in.wonderdraft_map"]
    with pytest.raises(ToolError):
        server.open_map(str(tmp_path / "missing.wonderdraft_map"))


def test_save_map_errors_and_backup(tmp_path):
    server.new_map(100, 100)
    with pytest.raises(ToolError, match="path"):
        server.save_map()
    p = tmp_path / "m.wonderdraft_map"
    server.save_map(str(p))
    r = server.save_map()
    assert r["backup"] is None  # this server wrote m.wonderdraft_map itself: no .bak
    assert not list(tmp_path.glob("*.bak"))
    r = server.save_map(str(tmp_path / "noext"))
    assert r["path"].endswith("noext.wonderdraft_map")


def test_render_preview_image_and_file(tmp_path):
    sample_map()
    server.add_label("Hello", [200, 100])
    out = server.render_preview(scale=0.5, show_ids=True)
    img, text = out
    assert isinstance(img, Image)
    content = img.to_image_content()
    assert content.mime_type == "image/png" if hasattr(content, "mime_type") else content.mimeType == "image/png"
    assert isinstance(text, str)
    line = [ln for ln in text.splitlines() if "png" in ln][0]
    path = Path(line.split("Saved:")[-1].strip())
    assert path.exists()
    with PILImage.open(path) as im:
        assert im.size == (200, 150)
    # unsaved map: written in the session temp dir
    assert path.parent.name.startswith("wdmcp-")

    mp = tmp_path / "saved.wonderdraft_map"
    server.save_map(str(mp))
    out = server.render_preview(bbox=[0, 0, 200, 150], scale=1.0)
    assert (tmp_path / "saved.preview.png").exists()
    with PILImage.open(tmp_path / "saved.preview.png") as im:
        assert im.size == (200, 150)
    assert "saved.preview.png" in out[1]


def test_server_registers_all_tools():
    import asyncio
    tools = asyncio.run(server.mcp.list_tools())
    names = {t.name for t in tools.tools} if hasattr(tools, "tools") else {t.name for t in tools}
    expected = {"new_map", "open_map", "save_map", "map_summary", "list_themes",
                "list_asset_sets", "list_objects", "paint_land", "generate_landmass",
                "raise_area", "add_lake", "add_river", "paint_ground", "add_symbol",
                "scatter_symbols", "add_mountain_range", "add_forest", "add_settlement",
                "add_label", "add_path", "add_territory", "update_object", "move_objects",
                "remove_objects", "recolor_symbols", "apply_theme", "set_frame", "render_preview",
                "undo"}
    assert expected <= names


# ------------------------------------------------------------------ real assets
needs_pck = pytest.mark.skipif(not Path(DEFAULT_PCK).is_file(), reason="Wonderdraft.pck not found")


@needs_pck
def test_real_catalog_new_map_and_preview(tmp_path):
    server.reset_session()  # lazy real catalog
    r = server.new_map(320, 200, theme="Adventure")
    assert "Adventure" in r["summary"]
    assert server.map_summary()["theme"] == "Adventure"
    server.generate_landmass(0.5, 1, "island")
    sets = server.list_asset_sets(category="symbols", limit=5)
    assert sets["total"] > 5 and len(sets["sets"]) == 5
    r = server.add_settlement("Home", [160, 100], "village")
    assert len(r["ids"]) == 2
    out = server.render_preview()
    assert isinstance(out[0], Image)
    assert "Adventure" in server.list_themes()["themes"]
    with pytest.raises(ToolError, match="Paper"):
        server.new_map(100, 100, theme="Papr")


def test_add_symbol_and_label_rotation_degrees():
    sample_map()
    r = server.add_symbol(TOWN, [200, 150], rotation=90)
    assert server.session().current.get(r["ids"][0])["rotation"] == pytest.approx(math.pi / 2)
    r = server.add_label("Side", [200, 150], rotation=90)
    assert server.session().current.get(r["ids"][0])["rotation"] == 90.0


def test_save_map_backup_once_and_brackets(tmp_path):
    import shutil
    from pathlib import Path
    p = tmp_path / "x[1].wonderdraft_map"
    shutil.copy(Path(__file__).parent / "fixtures" / "testmap.wonderdraft_map", p)
    server.open_map(str(p))
    r1 = server.save_map()
    assert r1["backup"] and Path(r1["backup"]).is_file() and Path(r1["path"]).is_absolute()
    server.add_label("Z", [10, 10])
    r2 = server.save_map()
    assert r2["backup"] is None
    assert len(list(tmp_path.glob("*.bak"))) == 1


def test_path_and_territory_report_clamped():
    sample_map()
    r = server.add_path([[-50, 10], [100, 100], [500, 100]])
    assert r["clamped"] == 2 and "clamped" in r["summary"]
    r = server.add_territory([[10, 10], [100, 10], [100, 100]], "#ff0000")
    assert r["clamped"] == 0
    with pytest.raises(ToolError, match="opacity"):
        server.add_territory([[10, 10], [100, 10], [100, 100]], "#ff0000", opacity=2)


def test_set_frame_validates_with_real_catalog():
    from pathlib import Path as _P
    from wdmcp.assets.catalog import DEFAULT_PCK, Catalog
    if not _P(DEFAULT_PCK).is_file():
        pytest.skip("Wonderdraft.pck not present")
    server.reset_session(Catalog.load(DEFAULT_PCK, _P("nonexistent-user-dir")))
    server.new_map(200, 100)
    with pytest.raises(ToolError, match="celtic"):
        server.set_frame(True, texture="celtic_dots")
    server.set_frame(True, texture="hatch_opaque")
    assert server.session().current.root["frame"]["texture"] == "hatch_opaque"
    with pytest.raises(ToolError, match="path_"):
        server.add_path([[10, 10], [50, 50]], style="dirt_road")


def test_style_names_listed_in_docs():
    from wdmcp import region
    from wdmcp.assets.catalog import BUILTIN_TEXTURES
    road = region.RegionSpec.model_json_schema()["$defs"]["Road"]["properties"]["style"]["description"]
    for n in BUILTIN_TEXTURES["paths"]:
        assert n in server.add_path.__doc__ and n in road
    assert "dirt_road" not in road
    for n in BUILTIN_TEXTURES["borders"]:
        assert n in server.add_territory.__doc__
    for n in BUILTIN_TEXTURES["frames"]:
        assert n in server.set_frame.__doc__


# ------------------------------------------------------------------ update_object types
def test_update_object_type_coercion_and_errors():
    sample_map()
    server.add_label("x", [10, 10])
    server.update_object("label:0", {"size": 30.0, "align": 2, "curve": 1, "z_index": 3})
    lab = server.session().current.get("label:0")
    assert lab["size"] == 30 and type(lab["size"]) is int
    assert lab["curve"] == 1.0 and type(lab["curve"]) is float
    for bad, msg in (({"size": 30.5}, "integer"), ({"size": "big"}, "integer"),
                     ({"size": True}, "integer"), ({"curve": True}, "number"),
                     ({"text": 5}, "string"), ({"size": 2 ** 70}, "64-bit"),
                     ({"color": None}, "colour"), ({"position": [1]}, r"\[x, y\]")):
        with pytest.raises(ToolError, match=msg):
            server.update_object("label:0", bad)
    assert server.session().current.get("label:0")["size"] == 30
    server.add_path([[10, 10], [50, 50]])
    with pytest.raises(ToolError, match="true or false"):
        server.update_object("path:0", {"straight": 1})
    server.update_object("path:0", {"straight": True, "noise_seed": 7})
    assert server.session().current.get("path:0")["straight"] is True


def test_update_object_symbol_variant_scale_and_packs():
    sample_map()
    r = server.add_symbol(TOWN, [200, 150], variant=0, scale=2)
    sid = r["ids"][0]
    server.update_object(sid, {"variant": 2})
    e = server.session().current.get(sid)
    assert e["texture"] == f"{TOWN}/v2"
    assert e["scale"] == Vector2(2.0, 2.0)  # multiplier kept
    server.update_object(sid, {"scale": 3})
    assert server.session().current.get(sid)["scale"] == Vector2(3.0, 3.0)
    with pytest.raises(ToolError, match="out of range"):
        server.update_object(sid, {"variant": 9})
    with pytest.raises(ToolError, match="v3"):
        server.update_object(sid, {"texture": f"{TOWN}/v3x"})
    # a different set via texture + variant brings its radius/type
    server.update_object(sid, {"texture": PEAK, "variant": 1})
    e = server.session().current.get(sid)
    assert e["texture"] == f"{PEAK}/v1" and e["type"] == "mountain" and e["radius"] == 30.0
    rows = server.list_objects("symbol")["objects"]
    assert rows[0]["scale"] == 3.0
    # a user-pack texture registers its pack and keeps the multiplier on the 0.25 base
    r = server.update_object(sid, {"texture": "user://assets/MyPack/sprites/symbols/S/a"})
    assert r["packs_registered"] == ["MyPack"]
    e = server.session().current.get(sid)
    assert "MyPack" in server.session().current.root["included_packs"]
    assert e["scale"] == Vector2(0.75, 0.75)
    server.undo()
    assert "MyPack" not in (server.session().current.root.get("included_packs") or [])


# ------------------------------------------------------------------ concurrency
def test_tool_calls_are_serialised():
    import threading
    import time
    from wdmcp.session import tool_errors
    active, overlap = [0], []

    @tool_errors
    def slow():
        active[0] += 1
        overlap.append(active[0])
        time.sleep(0.02)
        active[0] -= 1

    ts = [threading.Thread(target=lambda: [slow() for _ in range(5)]) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert overlap and max(overlap) == 1


def test_concurrent_mutating_tools_keep_state_consistent():
    import threading
    sample_map()
    errors = []

    def worker(k):
        try:
            for i in range(25):
                server.add_label(f"L{k}-{i}", [10 + i, 10 + k])
                server.add_symbol(TOWN, [100 + i, 100 + k], variant=0)
        except Exception as e:  # pragma: no cover
            errors.append(e)

    ts = [threading.Thread(target=worker, args=(k,)) for k in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errors
    doc = server.session().current
    assert len(doc.list("label")) == 100 and len(doc.list("symbol")) == 100
    assert len({e["text"] for _, e in doc.list("label")}) == 100
    assert len(server.session().history) == 20  # bounded undo intact


def test_replacing_maps_keeps_only_one_previous(session):
    server.new_map(100, 100)
    server.new_map(110, 100)
    server.new_map(120, 100)
    server.undo()
    assert session.current.width == 110
    with pytest.raises(ToolError, match="Nothing to undo"):
        server.undo()


def test_colour_0_255_rejected_with_hint():
    from wdmcp import convert
    with pytest.raises(ValueError, match="0-255.*#c81e1e"):
        convert.to_color([200, 30, 30])
    with pytest.raises(ValueError, match="between 0 and 1"):
        convert.to_color([-0.1, 0.5, 0.5])
    assert convert.to_color([1, 0.5, 0]) == Color(1.0, 0.5, 0.0, 1.0)
    sample_map()
    with pytest.raises(ToolError, match="0-255"):
        server.add_label("x", [10, 10], color=[255, 0, 0])


def test_seeds_must_be_non_negative():
    sample_map()
    with pytest.raises(ToolError, match="seed"):
        server.add_river([[100, 100], [200, 100]], seed=-1)
    with pytest.raises(ToolError, match="seed"):
        server.add_symbol(TOWN, [100, 100], seed=2**40)
    from pydantic import ValidationError
    from wdmcp import region
    with pytest.raises(ValidationError):
        region.RegionSpec(name="x", seed=-3)
    props = region.RegionSpec.model_json_schema()["properties"]["seed"]
    assert props["minimum"] == 0


def test_tool_schema_seed_minimum():
    import asyncio
    tools = {t.name: t for t in asyncio.run(server.mcp.list_tools())}
    schema = getattr(tools["add_river"], "inputSchema", None) or tools["add_river"].input_schema
    assert schema["properties"]["seed"]["minimum"] == 0


def test_preview_scale_capped_to_8192():
    sample_map()  # 400x300
    img, text = server.render_preview(scale=30)
    import io as _io
    from PIL import Image as _I
    im = _I.open(_io.BytesIO(img.data))
    assert max(im.size) <= 8192 and "capped" in text


def test_settlement_summary_wording():
    sample_map()
    r = server.add_settlement("Dreifurt", [200, 150], "town", set_key=TOWN)
    assert r["summary"].startswith(f"Placed town 'Dreifurt': symbol {r['ids'][0]} with its name "
                                   f"label {r['ids'][1]}")
    r = server.add_settlement("Quiet", [220, 150], "town", set_key=TOWN, label=False)
    assert "label" not in r["summary"].split(".")[0]


def test_paths_returned_are_absolute(tmp_path, monkeypatch):
    from pathlib import Path
    monkeypatch.chdir(tmp_path)
    sample_map()
    r = server.save_map("rel.wonderdraft_map")
    assert Path(r["path"]).is_absolute() and Path(r["path"]) == tmp_path / "rel.wonderdraft_map"
    r = server.open_map("rel.wonderdraft_map")
    assert Path(r["path"]).is_absolute()
    r = server.export_image("out.png", via="preview")
    assert Path(r["path"]).is_absolute() and (tmp_path / "out.png").is_file()


def test_settlement_name_with_braces():
    sample_map()
    r = server.add_settlement("{Old} Ford", [200, 150], "town", set_key=TOWN)
    assert "'{Old} Ford'" in r["summary"]
    assert server.session().current.get(r["ids"][1])["text"] == "{Old} Ford"


def test_recolor_symbols_tool_is_undoable():
    from wdmcp.assets.catalog import VariantMeta
    cat = server.session().catalog
    key = "res://sprites/symbols/paint_y"
    cat._sets[key] = SymbolSet(key, None, "symbols", "p", 50.0, (0.0, 0.0), "normal", [key + "/a"],
                               {"a": VariantMeta("A", 47.0, (-4.0, -23.0), "sample_color")})
    sample_map()
    sid = server.add_symbol(key, [200, 150])["ids"][0]
    assert server.session().current.get(sid)["sample"] is not None
    server.session().current.get(sid)["sample"] = None
    r = server.recolor_symbols()
    assert r["recolored"] == 1 and server.session().current.get(sid)["sample"] is not None
    server.undo()
    assert server.session().current.get(sid)["sample"] is None
    # changing the sprite to a normal one clears the tint
    server.recolor_symbols()
    server.update_object(sid, {"texture": TOWN + "/v0"})
    assert server.session().current.get(sid)["sample"] is None
