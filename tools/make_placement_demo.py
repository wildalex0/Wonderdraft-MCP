"""Build a 1920x1080 map exercising every placement op, for the GUI check.

Usage:
    python tools/make_placement_demo.py OUT.wonderdraft_map
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wdmcp.assets.catalog import Catalog  # noqa: E402
from wdmcp.model.document import MapDocument  # noqa: E402
from wdmcp.ops import place, terrain  # noqa: E402


def first(cat: Catalog, category: str, query: str, builtin: bool = True):
    for s in cat.sets(category, query):
        if s.key.startswith("res://sprites/") == builtin and (builtin or "T.A" in s.key):
            return s.key
    raise SystemExit(f"no {category} set matching {query!r}")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    cat = Catalog.load()
    doc = MapDocument.new(1920, 1080)
    continent = [(420, 300), (900, 180), (1450, 260), (1620, 560), (1380, 880),
                 (860, 920), (480, 740)]
    terrain.paint_land(doc, continent, roughness=0.6, seed=7)

    mt, d = place.mountain_range(doc, cat, first(cat, "mountains", "inked_mountains"),
                                 [(600, 420), (900, 360), (1200, 400)], 120, 4.0, seed=1)
    print("mountains:", len(mt), "dropped", d)
    fo, d = place.forest(doc, cat, first(cat, "trees", "inked_pine"),
                         [(700, 600), (1100, 580), (1150, 800), (750, 820)], 6.0, seed=2)
    print("forest:", len(fo), "dropped", d)
    print("built-in settlement:", place.add_settlement(doc, cat, "Stonebridge", (1300, 480), "town"))
    houses = first(cat, "symbols", "Houses", builtin=False)
    print("user-pack settlement:", houses, place.add_settlement(
        doc, cat, "Mossgrove", (560, 620), "village", set_key=houses))
    print("label:", place.add_label(doc, "The Reach", (950, 250), preset="Region", size=40))
    print("path:", place.add_path(doc, [(560, 650), (800, 700), (1050, 560), (1300, 500)],
                                  width=4, seed=3))
    print("territory:", place.add_territory(
        doc, [(1000, 300), (1500, 300), (1550, 600), (1250, 700), (1000, 550)], (0.7, 0.1, 0.1)))
    print("saved:", doc.save(Path(argv[1])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
