from pathlib import Path

from wdmcp.codec import load_map_bytes
from wdmcp.model.points import format_points, parse_points

FIX = Path(__file__).parent / "fixtures" / "testmap.wonderdraft_map"


def test_points_format_matches_godot():
    assert format_points([(526, 297), (539, 340)]) == "[ Vector2( 526, 297 ), Vector2( 539, 340 ) ]"
    assert format_points([(1.5, 2)]) == "[ Vector2( 1.5, 2 ) ]"


def test_points_parse_fixture_strings():
    r = load_map_bytes(FIX)
    strings = [p["points"] for p in r["paths"]] + [t["points"] for t in r["territories"]["territories"]]
    assert len(strings) == 7
    for s in strings:
        assert format_points(parse_points(s)) == s
