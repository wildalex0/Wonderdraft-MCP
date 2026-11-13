import json
import time
from pathlib import Path

import numpy as np
import pytest
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import ValidationError

from wdmcp import region, server
from wdmcp.assets.catalog import Catalog, DEFAULT_PCK, SymbolSet
from wdmcp.model import raster_spec as rs
from wdmcp.model.document import MapDocument

SAMPLE = Path(__file__).parents[1] / "examples" / "sample_region.json"
BOX = [[100, 60], [300, 60], [300, 240], [100, 240]]


def fake_catalog():
    c = Catalog(None, None)
    for key, cat, radius in (
            ("res://sprites/symbols/town_x", "symbols", 40.0),
            ("res://sprites/mountains/peaks_x", "mountains", 30.0),
            ("res://sprites/trees/oak_x", "trees", 12.0)):
        c._sets[key] = SymbolSet(key, None, cat, key.rsplit("/", 1)[-1], radius, (0.0, -25.0),
                                 "normal", [f"{key}/v{i}" for i in range(3)])
    c._themes = {"Adventure": {"water_hue": 0.5}}
    return c


@pytest.fixture(autouse=True)
def session():
    s = server.reset_session(fake_catalog())
    yield s
    server.reset_session()


def sample_spec() -> region.RegionSpec:
    return region.RegionSpec.model_validate_json(SAMPLE.read_text(encoding="utf-8"))


def small_spec(**extra) -> region.RegionSpec:
    return region.RegionSpec.model_validate(
        {"name": "T", "width": 400, "height": 300,
         "land": [{"polygon": BOX, "roughness": 0}], **extra})


def expected_counts(spec):
    return {"settlements": len(spec.settlements), "realms": len(spec.realms),
            "roads": len(spec.roads), "rivers": len(spec.rivers), "lakes": len(spec.lakes),
            "labels": len(spec.labels) + len(spec.realms) + len(spec.settlements)}


def test_sample_region_builds():
    spec = sample_spec()
    res = region.build(spec, fake_catalog())
    want = expected_counts(spec)
    for k, v in want.items():
        assert res.counts[k] == v, k
    doc = res.doc
    assert len(doc.list("territory")) == len(spec.realms)
    assert len(doc.list("path")) == len(spec.roads)
    assert len(doc.list("label")) == want["labels"]
    assert res.counts["mountain_symbols"] > 0 and res.counts["forest_symbols"] > 0
    assert (doc.width, doc.height) == (1920, 1080)
    assert region.build_region(spec, fake_catalog()).width == 1920


def test_build_is_deterministic():
    spec = sample_spec()
    a = region.build_region(spec, fake_catalog())
    b = region.build_region(spec, fake_catalog())
    assert np.array_equal(a.raster("mask"), b.raster("mask"))
    assert [e["position"] for _, e in a.list("symbol")] == [e["position"] for _, e in b.list("symbol")]


def test_settlement_on_water_is_moved_or_reported():
    spec = small_spec(settlements=[
        {"name": "Wetport", "kind": "port", "pos": [85, 150]},
        {"name": "Drownedhold", "kind": "ruin", "pos": [10, 10]},
        {"name": "Dryton", "kind": "town", "pos": [200, 150]}])
    res = region.build(spec, fake_catalog())
    text = " | ".join(res.warnings)
    assert "Wetport" in text and "moved" in text
    assert "Drownedhold" in text and "on water" in text
    assert "Dryton" not in text
    mask = res.doc.raster("mask")
    xs = sorted(round(e["position"].x) for _, e in res.doc.list("symbol"))
    assert 85 not in xs and 10 in xs  # Wetport moved, Drownedhold left in place
    wet = [e for _, e in res.doc.list("symbol") if round(e["position"].x) != 10
           and round(e["position"].x) != 200][0]
    assert rs.is_land(mask[int(wet["position"].y), int(wet["position"].x), 3])


def test_settlement_on_water_in_server_summary(tmp_path):
    spec = small_spec(settlements=[{"name": "Wetport", "kind": "port", "pos": [85, 150]}])
    out = server.build_region(spec, str(tmp_path / "t.wonderdraft_map"))
    assert "Wetport" in out["summary"] and any("Wetport" in w for w in out["warnings"])


def test_land_shape_needs_exactly_one():
    with pytest.raises(ValidationError):
        region.LandShape()
    with pytest.raises(ValidationError):
        region.LandShape(polygon=[[0, 0], [10, 0], [10, 10]], generate="island")
    region.LandShape(generate="island")
    with pytest.raises(ValidationError):
        region.RegionSpec.model_validate({"name": "x", "bogus": 1})


def test_colors_accept_hex_and_floats():
    poly = [[0, 0], [10, 0], [10, 10]]
    region.Realm(name="A", polygon=poly, color="#ff8800")
    region.Realm(name="A", polygon=poly, color=[1, 0.5, 0, 0.5])
    with pytest.raises(ValidationError):
        region.Realm(name="A", polygon=poly, color="red")


def test_centroid_is_area_weighted_with_fallback():
    poly = [[0, 0], [1, 0], [2, 0], [3, 0], [100, 0], [100, 100], [0, 100]]
    cx, cy = region.polygon_centroid(poly)
    assert abs(cx - 50) < 3 and abs(cy - 50) < 3
    assert region.polygon_centroid([[0, 0], [10, 10], [20, 20]]) == (10.0, 10.0)  # degenerate


def test_missing_default_set_falls_back_with_warning():
    res = region.build(small_spec(
        mountains=[{"polyline": [[100, 150], [300, 150]], "width": 40, "density": 3}]),
        fake_catalog())
    assert res.counts["mountain_symbols"] > 0
    assert any("default" in w and "peaks_x" in w for w in res.warnings)


def test_default_sets_shape():
    for scale in ("world", "region"):
        assert {"mountains", "hills", "forest"} <= set(region.DEFAULT_SETS[scale])


def test_schema_exported():
    schema = server.region_spec_schema()
    assert schema["type"] == "object" and "settlements" in schema["properties"]
    json.dumps(schema)
    assert schema["properties"]["name"].get("description")


def test_build_region_tool_saves_and_sets_session(tmp_path):
    spec = sample_spec()
    out = tmp_path / "r.wonderdraft_map"
    r = server.build_region(spec, str(out))
    assert out.is_file() and r["path"] == str(out)
    assert r["counts"]["settlements"] == len(spec.settlements)
    assert "render_preview" in r["summary"]
    assert server.session().current is not None and not server.session().dirty
    MapDocument.open(out)  # loads back
    again = server.build_region(spec, str(out))  # overwrite -> .bak
    assert list(tmp_path.glob("*.bak"))
    assert server.map_summary()["counts"]["territory"] == len(spec.realms)
    assert again["ids"]


def test_build_region_bad_set_key_is_toolerror(tmp_path):
    spec = region.RegionSpec.model_validate({
        "name": "T", "width": 400, "height": 300,
        "land": [{"generate": "island"}],
        "forests": [{"polygon": [[50, 50], [350, 50], [350, 250]], "density": 3,
                     "set_key": "res://nope"}]})
    with pytest.raises(ToolError):
        server.build_region(spec, str(tmp_path / "x.wonderdraft_map"))


@pytest.mark.skipif(not Path(DEFAULT_PCK).exists(), reason="Wonderdraft pck not installed")
def test_sample_builds_with_real_catalog_fast():
    cat = Catalog.load()
    for scale in ("world", "region"):
        for role in ("mountains", "hills", "forest"):
            cat.get_set(region.DEFAULT_SETS[scale][role])
    spec = sample_spec()
    t = time.perf_counter()
    res = region.build(spec, cat)
    assert time.perf_counter() - t < 10
    assert not [w for w in res.warnings if "default" in w]
