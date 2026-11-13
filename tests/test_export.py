import os
import sys
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from PIL import Image as PILImage

from test_server import fake_catalog, sample_map  # noqa: F401  (autouse session fixture below)
from wdmcp import server
from wdmcp.export import wonderdraft_app as wa
from wdmcp.export import ExportError, export_via_app, is_supported

FIXTURE = Path(__file__).parent / "fixtures" / "testmap.wonderdraft_map"


@pytest.fixture(autouse=True)
def session():
    s = server.reset_session(fake_catalog())
    yield s
    server.reset_session()


def saved_map(tmp_path):
    sample_map()
    server.save_map(str(tmp_path / "m.wonderdraft_map"))


# ------------------------------------------------------------- preview mode
def test_export_preview_fallback(tmp_path):
    sample_map()
    out = tmp_path / "out.png"
    r = server.export_image(str(out), via="preview")
    assert r["ids"] == [] and r["via"] == "preview" and r["path"] == str(out)
    assert (r["width"], r["height"]) == (400, 300)
    with PILImage.open(out) as im:
        assert im.format == "PNG" and im.size == (400, 300)


def test_export_preview_jpg(tmp_path):
    sample_map()
    out = tmp_path / "out.jpg"
    r = server.export_image(str(out), format="jpg", via="preview")
    with PILImage.open(out) as im:
        assert im.format == "JPEG" and im.size == (400, 300)
    assert r["width"] == 400


def test_export_refuses_existing_output(tmp_path):
    sample_map()
    out = tmp_path / "out.png"
    out.write_bytes(b"keep")
    with pytest.raises(ToolError, match="already exists"):
        server.export_image(str(out), via="preview")
    assert out.read_bytes() == b"keep"


def test_export_needs_open_map(tmp_path):
    with pytest.raises(ToolError, match="No map is open"):
        server.export_image(str(tmp_path / "o.png"), via="preview")


# ----------------------------------------------------------- wonderdraft mode
def test_wonderdraft_requires_saved_map(tmp_path):
    sample_map()
    with pytest.raises(ToolError, match="save_map"):
        server.export_image(str(tmp_path / "o.png"))


def test_wonderdraft_requires_clean_map(tmp_path):
    saved_map(tmp_path)
    server.add_label("X", [10, 10])
    with pytest.raises(ToolError, match="save_map"):
        server.export_image(str(tmp_path / "o.png"))


def test_unsupported_returns_ctrl_e_instructions(tmp_path, monkeypatch):
    saved_map(tmp_path)
    monkeypatch.setattr(server, "is_supported", lambda: False)
    r = server.export_image(str(tmp_path / "o.png"))
    assert "Ctrl+E" in r["summary"] and "m.wonderdraft_map" in r["summary"]
    assert 'via="preview"' in r["summary"]
    assert r["via"] == "instructions" and not (tmp_path / "o.png").exists()


def test_export_error_returns_instructions(tmp_path, monkeypatch):
    saved_map(tmp_path)
    monkeypatch.setattr(server, "is_supported", lambda: True)

    def boom(*a, **k):
        raise ExportError("Wonderdraft is already running")
    monkeypatch.setattr(server, "export_via_app", boom)
    r = server.export_image(str(tmp_path / "o.png"))
    assert "already running" in r["summary"] and "Ctrl+E" in r["summary"]


def test_wonderdraft_png_and_jpg_via_stub(tmp_path, monkeypatch):
    saved_map(tmp_path)
    monkeypatch.setattr(server, "is_supported", lambda: True)
    calls = []

    def fake(map_path, out_path, timeout=120):
        calls.append(Path(out_path))
        PILImage.new("RGBA", (64, 48), (10, 20, 30, 255)).save(out_path, format="PNG")
        return Path(out_path)
    monkeypatch.setattr(server, "export_via_app", fake)
    r = server.export_image(str(tmp_path / "a.png"))
    assert (r["width"], r["height"], r["via"]) == (64, 48, "wonderdraft")
    r = server.export_image(str(tmp_path / "b.jpg"), format="jpg")
    with PILImage.open(tmp_path / "b.jpg") as im:
        assert im.format == "JPEG"
    assert "converted" in r["summary"] and calls[1] != tmp_path / "b.jpg"


# ------------------------------------------------------------ pure helpers
def test_title_matches():
    assert wa.title_matches("My Map - Wonderdraft", "My Map")
    assert wa.title_matches("my map*", "My Map")
    assert not wa.title_matches("Wonderdraft", "My Map")
    assert not wa.title_matches("", "My Map")


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, d):
        self.t += d


def run_stable(sizes, deadline=100.0):
    c = Clock()
    it = iter(sizes)
    return wa.wait_for_stable_size(lambda: next(it, sizes[-1]), deadline=deadline,
                                   clock=c, sleep=c.sleep), c.t


def test_stable_size_waits_for_two_equal_polls():
    size, t = run_stable([None, 0, 10, 20, 20])
    assert size == 20 and t == pytest.approx(0.7 * 4)


def test_stable_size_ignores_empty_file():
    assert run_stable([0, 0, 0], deadline=5)[0] is None


def test_stable_size_times_out_when_always_growing():
    c = Clock()
    n = iter(range(1, 1000))
    assert wa.wait_for_stable_size(lambda: next(n), deadline=5, clock=c, sleep=c.sleep) is None


def test_is_supported_follows_exe(monkeypatch, tmp_path):
    exe = tmp_path / "Wonderdraft.exe"
    monkeypatch.setenv("WDMCP_EXE", str(exe))
    assert not is_supported()
    exe.write_bytes(b"")
    assert is_supported() == (sys.platform == "win32")


def test_export_via_app_refuses_when_running(monkeypatch, tmp_path):
    exe = tmp_path / "Wonderdraft.exe"
    exe.write_bytes(b"")
    monkeypatch.setenv("WDMCP_EXE", str(exe))
    monkeypatch.setattr(wa.sys, "platform", "win32")
    monkeypatch.setattr(wa, "_wonderdraft_running", lambda: True)
    m = tmp_path / "m.wonderdraft_map"
    m.write_bytes(b"x")
    with pytest.raises(ExportError, match="Close it first"):
        export_via_app(m, tmp_path / "o.png")


def test_input_struct_size():
    import ctypes
    if ctypes.sizeof(ctypes.c_void_p) != 8:
        pytest.skip("64-bit only")
    assert ctypes.sizeof(wa.INPUT) == 40


# ------------------------------------------------------------------ real GUI
@pytest.mark.gui
@pytest.mark.skipif(os.environ.get("WDMCP_GUI_TESTS") != "1",
                    reason="set WDMCP_GUI_TESTS=1 to drive the real Wonderdraft")
def test_export_via_app_end_to_end(tmp_path):
    assert is_supported(), "Wonderdraft not installed"
    out = export_via_app(FIXTURE, tmp_path / "gui.png")
    with PILImage.open(out) as im:
        assert im.format == "PNG" and im.width > 0
