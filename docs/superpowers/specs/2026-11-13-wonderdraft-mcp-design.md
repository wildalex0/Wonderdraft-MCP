# Wonderdraft MCP Server: Design

Date: 2026-11-13. Status: draft for review. The user waived the question round ("just start"). Every choice not quoted from the user is marked *(Claude's call)*.

## Intent

The user, a DM running several D&D campaigns kept in Obsidian vaults, wants to say *"build me a map of this region"*. Claude reads the user's notes and description and produces a world or region map. In the user's words: "an outputted image that you can freely manipulate, with all the features that Wonderdraft has to offer."

So the deliverable is **two linked things**:
1. A real `.wonderdraft_map` file, which the user can open in Wonderdraft and keep editing by hand.
2. A rendered image that Claude can look at, critique and change through further tool calls, in a loop.

## Goals

- An MCP server (stdio) that Claude Code can attach. It creates new maps and edits existing ones without the Wonderdraft GUI.
- Cover what Wonderdraft maps hold: landmass and elevation, coastline, water, lakes, rivers, ground colour, mountains, trees, symbols, paths and roads, territories and borders, labels, theme, frame, layers and asset packs (built-in and the user's own: Moulk's Megapack, T.A's Mapmaking Kit).
- Edit-oriented tools on a persistent open document: add, list, move and remove objects, repaint terrain, re-theme, save.
- `render_preview` returns an image to Claude after every change.
- `build_region(spec)` turns a structured region description (written by Claude from the user's notes) into a full first draft in one call.
- Final export through the real Wonderdraft app when it can be automated. Otherwise the user exports with Ctrl+E.

## Non-goals

- Re-implementing Wonderdraft's renderer pixel for pixel. The preview is for iteration and is allowed to look rougher.
- Wonderdraft 2.x or Godot 4 formats. The target is the installed 1.1.8 build (map `version = 15`).
- Driving the GUI for editing. All edits go through the file.
- Battle maps or city maps. The scope is world and region scale.

## Anti-goals (failure even if it "works")

- Corrupting or overwriting a user's map. Inputs are never modified. Saving over an existing file first writes a timestamped `.bak`.
- A file that Wonderdraft refuses to open or opens with silently lost data. The byte-exact round trip is a hard gate.
- Copying code from `tsiders/Wonderdraft-MCP-Server`. It has no licence. Use it only to cross-check the format.

## Constraints and known format facts (verified 2026-11-13 against `C:\Development\Wonderdraft\testmap.wonderdraft_map`)

- **Container:** Godot `FileAccessCompressed`. Layout: `"GCPF"` magic, `u32 mode` (0 = FastLZ), `u32 block_size` (4096), `u32 uncompressed_size`, then one `u32` compressed size per block, then the blocks, then a trailing `"GCPF"` magic.
- **Payload:** Godot 3 `store_var` encoding, i.e. a `u32` length followed by one binary Variant: a Dictionary with 28 keys.
  - Reals are 64-bit when float32 cannot hold the value exactly (e.g. `water_stain`).
  - Ints are 64-bit when outside the int32 range (e.g. `noise_seed`).
- **Top-level keys:** `version, map_width, map_height, water_level, water_flip_x, water_flip_y, water_offset, water_stain, windroses, theme, layers, paths, territories, symbols, labels, sharpen_labels, mask, ground, water_tint, trace, grid, frame, scale, boxes, load_default_symbols, load_default_not_symbols, included_default_packs, included_packs`.
- **Raster layers:** `mask`, `ground` and `water_tint` are Godot `Image` objects. Each is RGBA8 at the map size. In `mask`, alpha ranges over 44–255 and looks like elevation/coast, and RGB ∈ {0,1} on about 4% of pixels (meaning unknown). The meaning of these layers is **unverified**; see Spike B.
- **Points:** `paths[].points` and `territories[].points` are **strings** in Godot `str()` form: `'[ Vector2( 526, 297 ), Vector2( 539, 340 ) ]'`.
- **Symbol entries:** `texture` (a res path without extension), `position`, `rotation`, `scale`, `z_index`, `offset`, `radius`, `type` (`symbol|mountain|tree`), `mirror`, `outline_width`, `outline_color`, `sample`, `custom_colors`, `custom_color_mode`.
- **Label entries:** `position, rotation, text, font, size, color, outline_size, outline_color, glow_size, glow_color, align, curve, extra_spacing_char, z_index`. Theme `label_presets` holds Town, City, Water and Region.
- **Assets:**
  - `Wonderdraft.pck` (GDPC v1, Godot 3) is unencrypted. It contains 9 `.wonderdraft_theme` files (JSON), `res://sprites/...` and `res://packs/<pack>/sprites/{mountains,trees,symbols}/<set>/` with `.wonderdraft_image` files (plain PNG) and `.wonderdraft_symbols` files (JSON: name, radius, offset_x, offset_y, draw_mode), plus name CSVs (`names/settlements.csv`, `bodies_of_water.csv`).
  - The user's packs live in `%APPDATA%\Wonderdraft\assets\<pack>\sprites\...` as plain PNG files with a `.wonderdraft_symbols` file. How a placed user-pack symbol's `texture` is written is **unverified**; the assumption is `res://packs/<pack>/...`.
- **Platform:** Windows 10. Python 3.14 is installed. Wonderdraft is at `C:\Program Files\Wonderdraft\Wonderdraft.exe`.

## Decided how *(Claude's calls)*

- **Language and libraries:** Python ≥ 3.12, the official `mcp` SDK (FastMCP, stdio), `numpy`, `pillow`, `pydantic` and `pytest`. *Why:* a working Python decoder already exists, and raster terrain work wants NumPy.
- **Project location:** `C:\Development\Wonderdraft-MCP`. The test data stays in `C:\Development\Wonderdraft` and is copied into `tests/fixtures/`.
- **Codec:** a value-faithful Variant codec reproduces Godot's width rules, so `encode(decode(x)) == x` holds byte for byte. The writer emits FastLZ mode 0. A literal-only FastLZ encoder is acceptable for the first version; a real level-1 compressor is an optimisation.
- **Coordinates:** map pixels, origin top-left, matching Wonderdraft.
- **Object addressing:** stable ids `"<kind>:<index>"` (kinds: symbol, label, path, territory), returned by every mutating tool.
- **New maps** are built from a template derived from a Wonderdraft-saved blank map: lists cleared, rasters reset to "all water", and the size set by the caller.
- **Region input:** a pydantic `RegionSpec` (JSON). Claude fills it from the user's notes.
- **Preview:** a Pillow composite, defaulting to half resolution, returned as MCP image content and also written to disk next to the map.
- **Export:** launch Wonderdraft with the map and automate Ctrl+E if Spike C shows that works. The fallback is the preview PNG plus instructions for the user.

## Open questions to be closed by spikes (the plan schedules these first)

- **A:** Does Wonderdraft open a re-encoded map, and does it open one passed on the command line?
- **B:** What do the `mask` RGB/alpha channels, `ground` and `water_tint` mean? Where are rivers and lakes stored? What does a "no land" pixel look like? How is a user-pack symbol referenced?
- **C:** Can Wonderdraft's export be automated (keyboard into a Godot-drawn dialog)?

## Left to the builder

The internal module layout beyond what the plan names, the noise algorithms for coastlines, the scatter spacing heuristics, the preview's styling details, and the error message wording.
