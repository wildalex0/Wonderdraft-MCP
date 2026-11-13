# wdmcp

Python stdio MCP server for reading and writing Wonderdraft 1.1.8 `.wonderdraft_map` files.

## Install

```powershell
py -3.12 -m venv .venv          # Python >= 3.12
.venv\Scripts\pip install -e .[dev]
.venv\Scripts\python -m pytest  # unit tests, no Wonderdraft GUI needed
```

## Register with Claude Code

```powershell
claude mcp add wonderdraft -- C:\path\to\Wonderdraft-MCP\.venv\Scripts\wdmcp.exe
```

The server speaks MCP over stdio (logs go to stderr). `python tools/mcp_smoke.py` launches it
and drives a short tool flow as a self-check.

### Environment variables

| Variable | Default | Meaning |
| --- | --- | --- |
| `WDMCP_PCK` | `C:\Program Files\Wonderdraft\Wonderdraft.pck` | Wonderdraft's resource pack (built-in symbols, themes, names) |
| `WDMCP_USER_DIR` | `%APPDATA%\Wonderdraft\assets` | User asset packs |

## Tools

All coordinates are map pixels with the origin at the top left. Mutating tools return
`{"ids": [...], "summary": "..."}`. Symbol ids (`symbol:<n>`) are positional and shift when
symbols are inserted or removed; use the ids a tool returns, or `list_objects`.

- Map: `new_map`, `open_map` (read-only), `save_map` (timestamped `.bak` when overwriting),
  `map_summary`, `undo` (20 steps)
- Discovery: `list_themes`, `list_asset_sets`, `list_objects`
- Terrain: `paint_land`, `generate_landmass`, `raise_area`, `add_lake`, `add_river`, `paint_ground`
- Placement: `add_symbol`, `scatter_symbols`, `add_mountain_range`, `add_forest`,
  `add_settlement`, `add_label`, `add_path`, `add_territory`
- Editing: `update_object`, `move_objects`, `remove_objects`, `apply_theme`, `set_frame`
- Output: `render_preview` returns a PNG image and saves it (`<map>.preview.png` next to a saved
  map, otherwise in a temp dir)

Points are `[[x, y], ...]`; colours are `[r, g, b]` / `[r, g, b, a]` floats 0-1 or `"#rrggbb"`.
