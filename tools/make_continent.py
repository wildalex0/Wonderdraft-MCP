"""Build a 1920x1080 test map (continent, lake, river, ground stroke) for the GUI check.

Usage:
    python tools/make_continent.py OUT.wonderdraft_map
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wdmcp.model.document import MapDocument  # noqa: E402
from wdmcp.ops import terrain  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    doc = MapDocument.new(1920, 1080)
    continent = [(420, 300), (900, 180), (1450, 260), (1620, 560), (1380, 880),
                 (860, 920), (480, 740)]
    print("land px:", terrain.paint_land(doc, continent, roughness=0.6, seed=7))
    print("lake px:", terrain.add_lake(
        doc, [(1000, 450), (1120, 430), (1180, 520), (1090, 590), (990, 540)], seed=3))
    print("river px:", terrain.add_river(
        doc, [(1090, 590), (950, 700), (1000, 820), (1060, 960)], width=5, seed=4))
    print("ground px:", terrain.paint_ground(
        doc, [(560, 400), (800, 380), (820, 560), (580, 600)], 1, softness=0.6))
    print("saved:", doc.save(Path(argv[1])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
