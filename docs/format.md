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
