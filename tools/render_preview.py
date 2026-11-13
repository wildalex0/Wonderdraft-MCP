"""Render a .wonderdraft_map to PNG: render_preview.py IN OUT [--scale 0.5] [--ids]."""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wdmcp.assets.catalog import Catalog  # noqa: E402
from wdmcp.model.document import MapDocument  # noqa: E402
from wdmcp.render.preview import render_preview  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("out")
    ap.add_argument("--scale", type=float, default=0.5)
    ap.add_argument("--ids", action="store_true")
    a = ap.parse_args()
    doc = MapDocument.open(a.src)
    cat = Catalog.load()
    t = time.perf_counter()
    img = render_preview(doc, cat, scale=a.scale, show_ids=a.ids)
    dt = time.perf_counter() - t
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    img.save(a.out)
    print(f"{a.out} {img.size} {dt:.2f}s")


if __name__ == "__main__":
    main()
