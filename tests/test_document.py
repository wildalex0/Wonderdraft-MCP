import shutil
from pathlib import Path

import numpy as np
import pytest

from wdmcp.codec import gcpf, load_map_bytes
from wdmcp.model.document import MapDocument
from wdmcp.model.template import label_entry, path_entry, symbol_entry, territory_entry

FIX = Path(__file__).parent / "fixtures" / "testmap.wonderdraft_map"


def test_open_save_identity(tmp_path):
    doc = MapDocument.open(FIX)
    out = doc.save(tmp_path / "a.wonderdraft_map")
    assert gcpf.read(out.read_bytes()) == gcpf.read(FIX.read_bytes())


def test_save_existing_makes_backup(tmp_path):
    p = tmp_path / "m.wonderdraft_map"
    shutil.copy(FIX, p)
    before = p.read_bytes()
    doc = MapDocument.open(p)
    doc.add("label", label_entry("X", (10, 10)))
    doc.save()
    baks = list(tmp_path.glob("m.*.bak"))
    assert len(baks) == 1 and baks[0].read_bytes() == before
    assert len(MapDocument.open(p).list("label")) == 8


def test_save_without_path_raises():
    with pytest.raises(ValueError):
        MapDocument.new(100, 100).save()


def test_new_map_custom_size():
    doc = MapDocument.new(2400, 1600)
    r = doc.root
    for k in ("mask", "ground", "water_tint"):
        d = r[k].props["data"]
        assert (d["width"], d["height"], len(d["data"])) == (2400, 1600, 2400 * 1600 * 4)
    assert (r["map_width"], r["map_height"]) == (2400.0, 1600.0)
    assert r["symbols"] == [] and r["labels"] == [] and r["paths"] == []
    assert r["territories"]["territories"] == [] and r["windroses"] == [] and r["boxes"] == []
    assert (doc.width, doc.height) == (2400, 1600)


def test_new_map_does_not_share_template_state(tmp_path):
    a = MapDocument.new(64, 32)
    a.add("label", label_entry("A", (1, 1)))
    assert MapDocument.new(64, 32).root["labels"] == []
    out = a.save(tmp_path / "n.wonderdraft_map")
    assert load_map_bytes(out)["mask"].props["data"]["width"] == 64


def test_raster_edits_are_saved(tmp_path):
    doc = MapDocument.open(FIX)
    arr = doc.raster("ground")
    assert arr.shape == (1080, 1920, 4) and arr.flags.writeable
    arr[0, 0] = (1, 2, 3, 4)
    out = doc.save(tmp_path / "r.wonderdraft_map")
    back = MapDocument.open(out).raster("ground")
    assert tuple(back[0, 0]) == (1, 2, 3, 4)
    orig = np.frombuffer(
        load_map_bytes(FIX)["ground"].props["data"]["data"], np.uint8).reshape(1080, 1920, 4)
    assert (back[1:] == orig[1:]).all()


def test_ids_and_remove():
    doc = MapDocument.open(FIX)
    assert len(doc.list("symbol")) == 57
    first, second = doc.get("symbol:0"), doc.get("symbol:1")
    assert doc.remove(["symbol:1", "symbol:0"]) == 2
    assert len(doc.list("symbol")) == 55
    assert all(first is not s and second is not s for s in doc.root["symbols"])


def test_invalid_ids():
    doc = MapDocument.open(FIX)
    with pytest.raises(ValueError):
        doc.get("bogus:0")
    with pytest.raises(ValueError):
        doc.get("symbol")
    with pytest.raises(KeyError):
        doc.get("symbol:999")
    with pytest.raises(KeyError):
        doc.remove(["symbol:0", "symbol:999"])
    assert len(doc.list("symbol")) == 57


def test_add_update_and_bbox():
    doc = MapDocument.new(500, 500)
    s = doc.add("symbol", symbol_entry("res://x", (10, 10)))
    doc.add("symbol", symbol_entry("res://y", (400, 400)))
    p = doc.add("path", path_entry([(0, 0), (50, 50)]))
    t = doc.add("territory", territory_entry([(300, 300), (450, 450)]))
    assert s == "symbol:0" and p == "path:0" and t == "territory:0"
    doc.update(s, rotation=1.5)
    assert doc.get(s)["rotation"] == 1.5
    assert [i for i, _ in doc.list("symbol", (0, 0, 100, 100))] == ["symbol:0"]
    assert [i for i, _ in doc.list("path", (40, 40, 60, 60))] == ["path:0"]
    assert doc.list("path", (200, 200, 250, 250)) == []
    assert [i for i, _ in doc.list("territory", (400, 400, 500, 500))] == ["territory:0"]
