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
