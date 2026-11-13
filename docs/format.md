# Wonderdraft 1.1.8 map format: verified findings

Facts here were checked against the real app. Spec background: `docs/superpowers/specs/2026-11-13-wonderdraft-mcp-design.md`.

## Codec notes (Tasks 1–2)

- Container: `GCPF`, mode 0 (FastLZ), block 4096, uncompressed size, `ceil(n/4096)` block sizes (plus one zero-size entry when `n % 4096 == 0`), blocks, trailing `GCPF`.
- **INT is always written 64-bit** (flag set) in Wonderdraft's saves, including small values such as `version = 15`. All 196 ints in the test map use this. The spec's earlier "64-bit only outside int32" claim was wrong. Our encoder always writes int64.
- REAL is 64-bit only when float32 can't hold the value exactly.
- `ImageTexture` objects list the property `flags` twice. `GodotObject.pairs` keeps the ordered list so that the round trip stays byte-exact.
- A null Object (empty class name) appears in the data and is kept as `GodotObject("")`.

## Spike A: Wonderdraft accepts our output (2026-11-13)

Inputs: `tools/roundtrip.py tests/fixtures/testmap.wonderdraft_map out/rt_label.wonderdraft_map --label ROUNDTRIP`. Our encoder writes literal-only FastLZ, so the output is 26.6 MB against Wonderdraft's 2.37 MB.

| Question | Result |
|---|---|
| Does it open? | **Yes.** It renders identically to the original: all symbols, paths, territories, the box and the terrain are present. |
| Is the label visible as `ROUNDTRIP`? | **Yes.** |
| Does the command-line open work? | **Yes.** `Start-Process Wonderdraft.exe -ArgumentList "<path>"` opens the map directly. The window title becomes `<basename> - Wonderdraft`. It loads within 20 s. |
| Does it re-save without a warning? | **Yes.** No dialog appeared. The re-saved file is 2,373,268 bytes, and our codec decodes it and round-trips it byte-exactly. |

Differences Wonderdraft introduced on re-save (all benign):
- `boxes[0].__meta__.id` changed from 11 to 15. This is an internal node id that is regenerated on load.
- `included_default_packs` is recomputed on save. "T.A's Mapmaking Kit" was dropped because it isn't used on this map.

GUI automation notes, which feed into Spike C:
- `WScript.Shell.SendKeys("^s")` with the window activated had **no effect**.
- A synthetic mouse click (`SetCursorPos` + `mouse_event`) on the **Save** menu item **did** save.
- Screen coordinates: the virtual screen spans several monitors, so the screenshot origin is `VirtualScreen.Left/Top` (here −1080, 0). Map window coordinates come from `GetWindowRect`.

## Spike B: raster semantics (2026-11-13)

Synthetic probes (`tools/make_probe.py`) were opened in Wonderdraft. Captures use `tools/wd_launch.ps1`, which calls `PrintWindow` and runs DPI-aware, so the window can be captured while it is covered.

| Probe | Raster content | What Wonderdraft draws |
|---|---|---|
| a | mask alpha ramp 0→255 (left→right), RGB 0 | One straight coastline at **x = 49.7%**, i.e. alpha ≈ 127/128. Left (low alpha) is **water**: the water texture with the coastline hatch on the water side. Right is **land**: ground texture. There is no visible height shading. |
| b | alpha 255, RGB=1 stripe | All land. The RGB=1 stripe has **no visible effect**. |
| c | alpha 255, `ground` red square (A=255) | The red square is drawn opaquely over the land texture. `ground` is direct RGBA colour paint. |
| d | alpha 0 left / 255 right, `water_tint` blue square across the coast | Blue is drawn **only on the water side**, clipped at the coast, with a soft fade at the coastline. `water_tint` is direct RGBA tint over water. |
| e | ramp + RGB=1 horizontal band | Same as a. RGB=1 does not change land or water. |

Cross-check against the test map: thresholding its mask at `alpha >= 128` reproduces the rendered land and water exactly, including lakes and islands.

- **Land/water:** land ⇔ `mask.alpha >= 128` when `water_level = 0.5`. Alpha is a height field: water is 0–127, land is 128–255. The test map stores a noise field on both sides.
- **Brush plateaus seen in the test map:** 153 (painted land), 102 (erased to water), 255 (raised). In the raised stroke, RGB is also set to 1.
- **Mask RGB:** binary 0 or 1. It is set by the raise tool and has no rendering effect in our probes. We write 0, except to mirror the raise tool.
- **`ground`:** RGBA paint over land. Alpha is opacity, and (0,0,0,0) means no paint.
- **`water_tint`:** RGBA tint over water. (0,0,0,0) means none.

### Spike B, step 2: hand-drawn GUI probe (`tests/fixtures/probe_gui.wonderdraft_map`)

The user drew land, one river, one lake and two user-pack symbols in Wonderdraft and saved the map. Our codec round-trips it byte-exactly.

- **Rivers and lakes are mask R.** The **R channel of `mask`** holds anti-aliased freshwater coverage, 0–255. The river (with a thin, fading source) and the lake both live there. No `paths` entries are created.
  - Freshwater pixels occur only on land (alpha ≥ 128 under every R > 0 pixel).
  - G and B stay 0.
  - The R=G=B=1 pixels in the test map's raised stroke are freshwater coverage 1/255, which is effectively nothing.
- **User-pack symbol textures** have the form `user://assets/<pack>/sprites/<category>/<set>/<name>`, with no extension. Examples:
  - `user://assets/T.A's Mapmaking Kit/sprites/symbols/[T.A.] Houses/13`
  - `user://assets/Moulk's AI Fantasy Cartography Megapack/sprites/symbols/Mushroom Houses/Mushroom 5`

  `user://` is `%APPDATA%\Wonderdraft\`. The packs are listed in `included_packs`.
- Placed user symbols had `radius` 107/96, `offset` (0,0)/(0,−63) and `scale` 0.25 (Wonderdraft's default brush size). Both `draw_mode` and `custom_color_mode` are absent (None) on these entries.
- The `ground` and `water_tint` layers were untouched, so both stay all-zero.

## Task 7 GUI check: painted terrain renders in Wonderdraft (2026-11-13)

`tools/make_continent.py out/continent.wonderdraft_map` (blank template with theme Worn) was opened in Wonderdraft 1.1.8:
- The **continent** renders as land, with Wonderdraft's coastline outline and coastal effects. The coast forms at our alpha ≥ 128 edge as predicted.
- The **lake** and **river** written to mask R render as freshwater (theme freshwater colour plus outline). This confirms writing freshwater synthetically works.
- The **ground** stroke renders as a soft green patch over land.
- Quality note: with roughness 0.6 the coastline still reads as a polygon with gentle wobble, not a fractal coast, and the river looks like straight segments. This went to the Task 7 fix loop.

## Task 8 GUI check: placed objects render (2026-11-13)

`tools/make_placement_demo.py` (continent, built-in mountain range, built-in forest, built-in and user-pack settlements, a free label, a path and a territory) was opened in Wonderdraft 1.1.8.
- **Pack registration is required.** Wonderdraft loads user packs only when they are listed in `root["included_packs"]`. With the list empty, which is the blank template's default, a `user://assets/<pack>/...` symbol fails with "couldn't load" (the user saw this error). Built-in pack textures (`res://packs/<pack>/...`) correspond to `included_default_packs`. Placement now registers packs automatically.
- With registration in place, everything renders: mountains, trees, both settlements (built-in Stonebridge, T.A's-kit Mossgrove), labels in the theme preset font, the path (`path_blended`) and the territory (`border_dash`, fill at opacity). Settlement labels sit below the sprite: the label y is `pos.y + offset.y + sprite_h*scale/2 + 0.35*font size`.

## Task 9: preview vs Wonderdraft (2026-11-13)

`tools/render_preview.py` takes 0.19 s on the test map at scale 0.5. Compared side by side with Wonderdraft screenshots of the same maps:
- Land/water layout, coastline, mountains and clouds, symbol placement and scale, settlement labels, paths (dashed vs solid) and territory fill/outline all line up with Wonderdraft.
- Not reproduced, which is acceptable for an iteration preview: paper/water textures, vignette, coastal hatching, rounded territory corners (`smoothing`), and path/border texture styles.
- Readability gap: with the Worn theme, low land takes `ground_colors[0]` (olive), which is nearly the same colour as `water_colors[0]`, so coastal land and water blur together. This went to the fix loop.

## Spike C: automating Wonderdraft's PNG export (2026-11-13)

**Result: reliable, 3/3 runs.** Each run took about 9.5 s for a 1920×1080 map and produced byte-identical 3,821,591-byte RGBA PNGs (`tools/wd_export.ps1`).

Sequence:
1. `Start-Process Wonderdraft.exe "<map path>"`. Wait until the main window title starts with `<map stem>` (about 1.1 s), then allow about 3 s for the first canvas render.
2. **Ctrl+E** sent with `SendInput` (virtual key + scan code) to the foreground Wonderdraft window. `WScript.Shell.SendKeys` does **not** work: Godot ignores it. This opens Wonderdraft's own (Godot-drawn) **Export Options** dialog (Image: Normal, Format: PNG, Export Trace Image, OK/Cancel).
3. **Enter** (`SendInput`) confirms it with the defaults. No mouse is needed. A synthetic click on OK also works when the window really is in the foreground.
4. Wonderdraft then opens a **native Windows save dialog**: top-level `#32770` window titled `Export`, owned by the Wonderdraft process. It can be driven without focus or mouse:
   - `WM_SETTEXT` with the full output path on the File name edit (class `Edit`, control id **1001**; the address bar's edit has id 41477)
   - `BM_CLICK` on the Save button (control id **1**)
5. Poll until the output file exists and its size is stable, then `CloseMainWindow` (force-kill after 5 s).

Caveats:
- Steps 2–3 need Wonderdraft to be the **foreground** window and send real keystrokes, so typing during those ~2 s can interfere.
- Windows' foreground-lock can refuse `SetForegroundWindow`. When the native dialog is open, refocusing the main window sends keystrokes to it instead of the dialog, which is why step 4 uses window messages.
- Capturing the native dialog needs `CopyFromScreen` on its own rect, because `PrintWindow` on the main window doesn't include it.
- Godot draws the Export Options dialog itself, so UIA/pywinauto can't see its controls (as expected). Only keystrokes or clicks work there.
- **Python port (`wdmcp.export.wonderdraft_app.export_via_app`), live-tested 3/3, about 17 s each.**
  - Keys must be sent as one `SendInput` per event, about 40 ms apart, because Godot polls input once per frame and loses a press+release that arrive in the same frame. Allow 300 ms after foregrounding before sending.
  - Readiness matters. Sent 3 s after the window title appears, the first Ctrl+E was always ignored. After polling `SendMessageTimeoutW(WM_NULL)` and waiting `READY_SECONDS = 6` from the title, the first attempt succeeded in **3/3 runs, about 12.8 s each**. A guarded single retry (Esc, then Ctrl+E, then Enter, only if no native dialog exists yet) remains as a fallback.
  - Wonderdraft is launched with stdin/stdout/stderr set to DEVNULL so it can't write into the MCP JSON-RPC pipe.

## Task 13: end-to-end acceptance on the Dreifurt notes (2026-11-13)

**Result: pass.** A fresh headless Claude Code session (`claude -p`) with the `wonderdraft` server attached took 146 turns, ~12 min and $5.01. It had read-only file tools and read the user's New Campaign vault without modifying it. Then it:
- wrote a 3000×2400 `RegionSpec` from *The Dreifurt Region*, the four quarter pages, *Villages*, *Surroundings* and *Travel and Distances*, at 27 px per road mile from the Roadstone;
- called `build_region` and iterated three times with `render_preview` plus edit tools (moved labels, re-scattered hills, re-added a missing river stretch, swapped sprites, cleared clutter), saving after each round;
- exported a full-scale preview.

The controller then opened the map in **Wonderdraft 1.1.8** and exported it with `export_via_app`, which took 21.5 s and succeeded on the second attempt. The result is a 3000×2400 PNG with the four quarters as territories, roads, the Aldengau/Eisenmark/Irmenwald rivers, Irmenwald forest, upland hills, all towns, villages and ranked sites, and labels with theme fonts and frame. Everything is present, and the objects are ordinary Wonderdraft entries, so they stay editable. Outputs are in `out/dreifurt/`, which is not committed.

## Known gaps (from the acceptance run and controller checks)
1. **Map size.** Saved maps are about 89 MB at 3000×2400 because `fastlz.compress` is literal-only; Wonderdraft's own saves are about 30× smaller. A real FastLZ level-1 compressor is needed.
2. **Backup clutter.** Every `save_map` onto the same path writes another `.bak`. The acceptance run left three 89 MB backups.
3. **Label rotation units.** Tools and schema say degrees, but the value is stored as Godot radians, so small numbers spin text.
4. **`build_region` dropped a river stretch silently.** The Aldengau's first segment from the city did not draw, and there was no warning. Re-adding it with `add_river` worked.
5. **`update_object` can't change a symbol's variant.** Guessing a `texture` name picked the wrong sprite. Remove plus `add_symbol(variant=…)` works.
6. **Missing knobs.** `add_mountain_range` has no symbol scale, and river width can't be edited after building.
7. **Scale-appropriate default sets.** The default "hills" are tiny tufts and the default mountains are alpine peaks.
8. **Export on large maps.** The first Ctrl+E is still sometimes ignored; the guarded retry covers it.
9. **Not representable from the notes.** Off-map destinations such as the sea port appear only as edge labels.

## Rotation units, GUI-pinned (2026-11-13)

Probe maps (`out/rotprobe*.wonderdraft_map`) were opened in Wonderdraft 1.1.8:
- **Label `rotation` is stored in degrees**, clockwise on screen. 30 shows a 30° tilt, 90 is vertical, and 0.5 and 1.5708 look almost flat.
- **Symbol `rotation` is stored in radians**, clockwise. 0.5 tilts about 29°, 1.5708 is vertical, and 30 and 90 wrap around (30 rad ≈ 278°).
- Tools take **degrees** for both and convert to radians when writing symbols. The preview must read labels as degrees and symbols as radians.
