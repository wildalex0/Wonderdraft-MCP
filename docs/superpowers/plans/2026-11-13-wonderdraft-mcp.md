# Wonderdraft MCP Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a stdio MCP server that creates and edits real Wonderdraft 1.1.8 `.wonderdraft_map` files from region descriptions. After every change it returns a preview image so Claude can keep iterating.

**Architecture:** The layers, bottom to top:
1. A byte-exact codec: the GCPF/FastLZ container and Godot 3 binary Variants.
2. A `MapDocument` model over the decoded dictionary, with NumPy views of the raster layers.
3. An asset catalogue built from `Wonderdraft.pck` and the user's asset folder.
4. Terrain and placement operations.
5. A Pillow preview renderer.
6. FastMCP tools, including a one-shot `build_region(spec)`.

Three spikes against the real app settle the unknowns before any generator code is written.

**Tech Stack:** Python ≥ 3.12, `mcp` (FastMCP), `numpy`, `pillow`, `pydantic` v2, `pytest`. Windows 10. Wonderdraft 1.1.8 lives at `C:\Program Files\Wonderdraft`.

**Spec:** `docs/superpowers/specs/2026-11-13-wonderdraft-mcp-design.md`. Recon artefacts are in `research/`: the prototype decoders `gcpf.py` and `variant.py`, the dumped `schema.txt`, and `pck_paths.txt`.

## Global Constraints

- Python ≥ 3.12. Runtime dependencies are limited to `mcp`, `numpy`, `pillow` and `pydantic`. Dev dependencies: `pytest`.
- Package `wdmcp` lives under `src/wdmcp/`. Tests live under `tests/`. Fixtures live under `tests/fixtures/`, holding a **copy** of `C:\Development\Wonderdraft\testmap.wonderdraft_map`.
- Never modify an input map. Saving onto an existing path first copies it to `<name>.<YYYYMMDD-HHMMSS>.bak`.
- `encode(decode(payload)) == payload` byte for byte for every fixture. This holds for the whole project.
- Coordinates are map pixels, origin top-left. Object ids are `"<kind>:<index>"` with kind ∈ `symbol|label|path|territory`.
- Unit tests must not need the Wonderdraft GUI. Steps that use the GUI are marked **[GUI]** and record their result in `docs/format.md`.
- Do not copy code from `github.com/tsiders/Wonderdraft-MCP-Server`. It has no licence.
- Built-in asset paths come from `C:\Program Files\Wonderdraft\Wonderdraft.pck`. User packs come from `%APPDATA%\Wonderdraft\assets`. Both roots can be overridden with the env vars `WDMCP_PCK` and `WDMCP_USER_DIR`.

## Review Focus

1. **Map sizes other than 1920×1080.** Each raster `Image` must take `width`/`height` equal to `map_width`/`map_height`, with a data length of `w*h*4`. Test: `test_new_map_custom_size` (Task 4).
2. **Non-ASCII label text** (e.g. "Dreifurt", "Ödmark", "Straße"). Variant strings use the UTF-8 byte length plus 4-byte padding. Test: `test_unicode_label_roundtrip` (Task 2).
3. **Saving to an existing file.** A backup must be made, and the input file must stay byte-identical. Test: `test_save_existing_makes_backup` (Task 4).
4. **Unknown asset or theme names.** Raise `AssetNotFound` with up to 5 close matches, never a bare KeyError. Test: `test_unknown_set_suggests` (Task 6).
5. **Coordinates outside the map.** Placement ops clip polygons and points to the map bounds and report how many items were dropped. Test: `test_scatter_clips_out_of_bounds` (Task 8).

---

### Task 1: Project scaffold and GCPF/FastLZ container

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `README.md`, `src/wdmcp/__init__.py`, `src/wdmcp/codec/__init__.py`, `src/wdmcp/codec/fastlz.py`, `src/wdmcp/codec/gcpf.py`
- Create: `tests/fixtures/testmap.wonderdraft_map` (copy), `tests/test_gcpf.py`

**Interfaces:**
- Produces:
  - `fastlz.decompress(src: bytes) -> bytes` (levels 1 and 2)
  - `fastlz.compress(src: bytes) -> bytes` (valid level-1 stream; literal-only is acceptable)
  - `gcpf.read(data: bytes) -> bytes` (the payload)
  - `gcpf.write(payload: bytes, block_size: int = 4096) -> bytes`
  - `class GcpfError(ValueError)`

- [ ] **Step 1:** Run `git init`. Write `pyproject.toml` (hatchling, src layout, `[project.scripts] wdmcp = "wdmcp.server:main"`). Create a venv with `python -m venv .venv` and install `-e .[dev]`.
- [ ] **Step 2: Write failing tests**

```python
FIX = Path(__file__).parent / "fixtures" / "testmap.wonderdraft_map"
def test_read_testmap_size():
    assert len(gcpf.read(FIX.read_bytes())) == 25_791_944
def test_write_read_roundtrip():
    payload = gcpf.read(FIX.read_bytes())
    out = gcpf.write(payload)
    assert out[:4] == b"GCPF" and out[-4:] == b"GCPF"
    assert struct.unpack_from("<III", out, 4) == (0, 4096, len(payload))
    assert gcpf.read(out) == payload
def test_fastlz_roundtrip_small_and_edge():
    for s in [b"", b"a", b"x" * 4096, bytes(range(256)) * 16]:
        assert fastlz.decompress(fastlz.compress(s)) == s if s else True
def test_bad_magic_raises():
    with pytest.raises(gcpf.GcpfError): gcpf.read(b"NOPE" + b"\0" * 32)
```

- [ ] **Step 3:** Run `pytest tests/test_gcpf.py -v`. Expected: FAIL (module missing).
- [ ] **Step 4:** Implement by porting `research/gcpf.py`.
  - Block-count rule: `ceil(total/block)` sizes, plus one extra **only when `total % block == 0`**. This matches Godot 3's writer. Verify it against the fixture's header, then also test a payload of exactly 4096 bytes.
  - The writer appends the trailing `GCPF` magic.
  - Literal-only `compress`: emit runs of at most 32 bytes, each prefixed by a control byte `len-1` (0–31).
- [ ] **Step 5:** Run `pytest -v`. Expected: PASS.
- [ ] **Step 6:** Commit with the message `feat: GCPF/FastLZ container codec`.

### Task 2: Byte-exact Godot 3 Variant codec

**Files:**
- Create: `src/wdmcp/codec/variant.py`, `src/wdmcp/codec/types.py`, `tests/test_variant.py`

**Interfaces:**
- Consumes: `gcpf.read`
- Produces:
  - `types.py`: frozen dataclasses `Vector2(x: float, y: float)`, `Rect2`, `Color(r, g, b, a)`, `Transform2D`, `NodePath(names: list[str], subnames: list[str], absolute: bool)`, `GodotObject(cls: str, props: dict)`, `PoolReal(list[float])`, `PoolInt`, `PoolColor`, `PoolVector2`, `PoolString`. `PoolByteArray` is plain `bytes`.
  - `variant.decode(payload: bytes) -> Any`: strips the `u32` length prefix and asserts it covers the whole payload.
  - `variant.encode(value: Any) -> bytes`: adds the prefix.
  - `load_map_bytes(path) -> dict` and `dump_map_bytes(root: dict) -> bytes` (gcpf plus variant).

- [ ] **Step 1: Write failing tests**

```python
def test_testmap_byte_exact():
    payload = gcpf.read(FIX.read_bytes())
    assert variant.encode(variant.decode(payload)) == payload
def test_real_width_rule():  # Godot: 64-bit iff float32 can't hold it exactly
    assert len(variant.encode(0.5)) == 4 + 8 and len(variant.encode(0.47001155663049116)) == 4 + 12
def test_int_width_rule():
    assert len(variant.encode(15)) == 4 + 8 and len(variant.encode(4227431513)) == 4 + 12
def test_known_values():
    root = variant.decode(gcpf.read(FIX.read_bytes()))
    assert root["version"] == 15 and root["map_width"] == 1920.0
    assert root["paths"][0]["noise_seed"] == 4227431513
    assert root["mask"].cls == "Image" and root["mask"].props["data"]["width"] == 1920
def test_unicode_label_roundtrip():
    v = {"text": "Ödmark Straße ✦"}
    assert variant.decode(variant.encode(v)) == v
```

- [ ] **Step 2:** Run `pytest tests/test_variant.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement by porting the decoder in `research/variant.py` to typed values and adding the mirror encoder. Rules:
  - The header is `type | flags<<16`.
  - REAL sets `flags=1` and writes f64 iff `struct.unpack('<f', struct.pack('<f', x))[0] != x`.
  - INT sets `flags=1` and writes i64 iff the value is outside int32.
  - Python `bool` must be checked before `int`.
  - Strings: `u32` byte length, then UTF-8, padded to 4 bytes.
  - Dictionaries and arrays keep insertion order.
  - OBJECT: class-name string, `u32` property count, then (name string, value) pairs.
  - NodePath uses the new format (`0x80000000 | names`, subnames, flags).
  - `None` encodes as NIL.
- [ ] **Step 4:** Run `pytest -v`. Expected: PASS. `test_testmap_byte_exact` is the gate; do not continue until it passes.
- [ ] **Step 5:** Commit with the message `feat: byte-exact Godot 3 Variant codec`.

### Task 3: Spike A [GUI]: Wonderdraft accepts our output

**Files:**
- Create: `tools/roundtrip.py`, `tools/wd_launch.ps1`, `docs/format.md`

- [ ] **Step 1:** Write `tools/roundtrip.py IN OUT`, which runs `dump_map_bytes(load_map_bytes(IN))` and writes the result to a **new** path. Then make a variant that changes the first label's text to `ROUNDTRIP`.
- [ ] **Step 2:** Write `tools/wd_launch.ps1 -Map <path> -Shot <png>`.
  - It runs `Start-Process "C:\Program Files\Wonderdraft\Wonderdraft.exe" -ArgumentList "<path>"`, waits 15 s, and saves a full-screen capture with `System.Drawing` `CopyFromScreen`.
  - Read the PNG. Does the map open directly from the command-line argument?
  - If not, open it by hand: Ctrl+O, type the path into the Godot dialog, press Enter.
- [ ] **Step 3:** Record the results in `docs/format.md`, section "Spike A":
  - Did it open?
  - Is the label visible as `ROUNDTRIP`?
  - Does the command-line open work?
  - Does Wonderdraft re-save it without a warning?
  - **Gate:** if Wonderdraft rejects the file, stop and debug the codec first (superpowers:systematic-debugging).
- [ ] **Step 4:** Commit with the message `spike: verify Wonderdraft opens re-encoded maps`.

### Task 4: MapDocument model, points strings and new-map template

**Files:**
- Create: `src/wdmcp/model/__init__.py`, `src/wdmcp/model/points.py`, `src/wdmcp/model/document.py`, `src/wdmcp/model/template.py`
- Create: `tests/test_points.py`, `tests/test_document.py`

**Interfaces:**
- Consumes: `load_map_bytes`, `dump_map_bytes`, `types.*`
- Produces:
  - `points.format_points(pts: list[tuple[float, float]]) -> str` and `points.parse_points(s: str) -> list[tuple[float, float]]`
  - `class MapDocument`:
    - `open(path) -> MapDocument` (classmethod) and `new(width: int, height: int, theme: str | None = None) -> MapDocument` (classmethod)
    - `.width`, `.height`, `.root: dict`, `.path: Path | None`
    - `raster(name: Literal["mask","ground","water_tint"]) -> np.ndarray`: a writable `(h, w, 4)` uint8 view. It is synced back on save.
    - `add(kind, entry: dict) -> str` (returns an id), `get(id) -> dict`, `update(id, **fields)`, `remove(ids: list[str]) -> int`, `list(kind, bbox=None) -> list[tuple[str, dict]]`
    - `save(path: Path | None = None) -> Path`
  - `template.symbol_entry(...)`, `template.label_entry(...)`, `template.path_entry(...)`, `template.territory_entry(...)`: dict factories whose keys and defaults are copied from the fixture entries listed in the spec.

- [ ] **Step 1: Write failing tests**

```python
def test_points_format_matches_godot():
    assert format_points([(526, 297), (539, 340)]) == "[ Vector2( 526, 297 ), Vector2( 539, 340 ) ]"
    assert format_points([(1.5, 2)]) == "[ Vector2( 1.5, 2 ) ]"
def test_points_parse_fixture_strings():  # every path/territory string in the fixture
    for s in fixture_point_strings(): assert format_points(parse_points(s)) == s
def test_open_save_identity(tmp_path):
    doc = MapDocument.open(FIX); out = doc.save(tmp_path / "a.wonderdraft_map")
    assert gcpf.read(out.read_bytes()) == gcpf.read(FIX.read_bytes())
def test_save_existing_makes_backup(tmp_path):
    p = tmp_path / "m.wonderdraft_map"; shutil.copy(FIX, p); before = p.read_bytes()
    doc = MapDocument.open(p); doc.add("label", label_entry("X", (10, 10))); doc.save()
    assert len(list(tmp_path.glob("m.*.bak"))) == 1 and list(tmp_path.glob("m.*.bak"))[0].read_bytes() == before
def test_new_map_custom_size():
    doc = MapDocument.new(2400, 1600); r = doc.root
    for k in ("mask", "ground", "water_tint"):
        d = r[k].props["data"]; assert (d["width"], d["height"], len(d["data"])) == (2400, 1600, 2400 * 1600 * 4)
    assert (r["map_width"], r["map_height"]) == (2400.0, 1600.0) and r["symbols"] == [] and r["labels"] == []
def test_ids_and_remove():
    doc = MapDocument.open(FIX); assert len(doc.list("symbol")) == 57
    assert doc.remove(["symbol:0", "symbol:1"]) == 2 and len(doc.list("symbol")) == 55
```

- [ ] **Step 2:** Run the tests. Expected: FAIL.
- [ ] **Step 3:** Implement. `MapDocument.new` deep-copies the fixture-derived template (`src/wdmcp/model/blank.wonderdraft_map`, created in Task 5 Step 4; until then, derive it from the test fixture) and makes these changes:
  - Clears `symbols`, `labels`, `paths`, `territories.territories`, `windroses` and `boxes`.
  - Sets the size.
  - Allocates the rasters with the "all water" fill from `docs/format.md`. Use alpha 0 until Spike B settles it.

  Number formatting in `format_points`: integral floats print with no decimal point; other values use Python `repr` with trailing zeros stripped. Check the result against every fixture string.
- [ ] **Step 4:** Run `pytest -v`. Expected: PASS.
- [ ] **Step 5:** Commit with the message `feat: MapDocument model with safe save and templates`.

### Task 5: Spike B [GUI]: terrain raster semantics, rivers, user-pack references, blank template

**Files:**
- Create: `tools/make_probe.py`, `tests/fixtures/probe_*.wonderdraft_map`
- Modify: `docs/format.md`, `src/wdmcp/model/template.py`
- Create: `src/wdmcp/model/blank.wonderdraft_map`, `src/wdmcp/model/raster_spec.py`

**Interfaces:**
- Produces: in `raster_spec.py`, the constants and functions the terrain code depends on:
  - `WATER_FILL: tuple[int, int, int, int]` (RGBA)
  - `land_alpha(height01: np.ndarray) -> np.ndarray`
  - `SEA_LEVEL_ALPHA: int`
  - `RIVER_ENCODING: str` (a description) plus `paint_river_pixels(mask, poly, width)` **or** `river_path_entry(...)`, depending on what the probe shows
  - `GROUND_ENCODING`
  - `user_pack_texture(pack: str, rel: str) -> str`

- [ ] **Step 1:** Build synthetic probes with `tools/make_probe.py` and open each one through `wd_launch.ps1`:
  - (a) mask alpha as a left-to-right ramp 0→255, RGB 0
  - (b) alpha 255 everywhere, RGB=1 in a vertical stripe
  - (c) `ground` with a red square, alpha 255
  - (d) `water_tint` with a blue square

  Read the screenshots. Where is the coastline on the ramp? What does RGB=1 do? Note these against `water_level = 0.5`.
- [ ] **Step 2:** Make a GUI probe map. Try `pywinauto` keyboard and mouse automation first. If the canvas does not take synthetic input, ask the user for a two-minute favour. The map is new, 1920×1080, Adventure theme. Draw one landmass (Z), one river (R), one lake (lake tool), one ground-colour stroke (G), and place one Moulk's-pack symbol and one T.A's-pack symbol. Save it as `tests/fixtures/probe_gui.wonderdraft_map`. Diff it against a blank save, `probe_blank.wonderdraft_map`.
- [ ] **Step 3:** Write down the answers in `docs/format.md` "Spike B" and encode them in `raster_spec.py`:
  - how land and height are encoded
  - where rivers and lakes live (mask RGB? a separate key?)
  - the ground and tint encoding
  - the exact `texture` string for user-pack symbols
- [ ] **Step 4:** Copy `probe_blank` to `src/wdmcp/model/blank.wonderdraft_map` and switch `MapDocument.new` to use it.
- [ ] **Step 5:** Add tests that pin the findings:
  - `test_blank_template_is_all_water`
  - `test_user_pack_texture_matches_probe`, which asserts `user_pack_texture(...)` equals the string found in `probe_gui`
- [ ] **Step 6:** Run `pytest -v`. Expected: PASS.
- [ ] **Step 7:** Commit with the message `spike: pin raster semantics and pack references`.

*Optional, only if the probes leave something ambiguous:* read identifier and constant tables from `scripts/save/save_system.gdc` and `scripts/tools/{paint_landmass,raise_landmass,freshwater_brush,river_tool}.gdc` in the pck. Record only the facts learned, not the code.

### Task 6: Asset catalogue (pck, user packs, themes, names)

**Files:**
- Create: `src/wdmcp/assets/pck.py`, `src/wdmcp/assets/catalog.py`, `src/wdmcp/assets/themes.py`, `tests/test_assets.py`

**Interfaces:**
- Produces:
  - `class PckReader(path)` with `.paths() -> list[str]` and `.read(res_path: str) -> bytes`. The format is GDPC v1: a 4+16+64-byte header, `u32 count`, then for each entry `u32 len` + path + `u64 off` + `u64 size` + 16-byte md5.
  - `@dataclass SymbolSet(key: str, pack: str | None, category: Literal["symbols","mountains","trees"], name: str, radius: float, offset: tuple[float, float], draw_mode: str, variants: list[str])`. `variants` holds res texture paths with no extension.
  - `class Catalog`:
    - `load(pck_path, user_dir) -> Catalog` (classmethod)
    - `.sets(category=None, query=None) -> list[SymbolSet]`
    - `.get_set(key) -> SymbolSet`, which raises `AssetNotFound(key, suggestions)`
    - `.image(texture: str) -> PIL.Image`, which resolves pck `.wonderdraft_image` (PNG) or a user-folder `.png`
    - `.themes() -> dict[str, dict]`
    - `.name_lists() -> dict[str, list[str]]`
  - `themes.apply_theme(doc: MapDocument, theme_json: dict) -> None`, which maps the theme JSON onto `root["theme"]` (keys in common; record any renames in `docs/format.md`).

- [ ] **Step 1: Write failing tests** (skipped when the pck is absent):

```python
def test_pck_lists_themes():
    assert {"Adventure", "Pastel", "Terra"} <= set(cat.themes())
def test_sets_include_builtin_and_user():
    keys = [s.key for s in cat.sets("mountains")]
    assert any(k.startswith("res://sprites/mountains/") for k in keys)
    assert any("Moulk's AI Fantasy Cartography Megapack" in k for k in keys)
def test_image_loads_png():
    s = cat.sets("symbols")[0]; assert cat.image(s.variants[0]).mode in ("RGBA", "LA", "P", "RGB")
def test_unknown_set_suggests():
    with pytest.raises(AssetNotFound) as e: cat.get_set("penned_mountians_large")
    assert any("penned_mountains_large" in s for s in e.value.suggestions)
def test_apply_theme_changes_label_presets():
    doc = MapDocument.new(800, 600); apply_theme(doc, cat.themes()["Pastel"])
    assert set(doc.root["theme"]["label_presets"]) >= {"Town", "City", "Water", "Region"}
```

- [ ] **Step 2:** Run the tests and confirm they FAIL. Then implement.
  - Set key = the res directory path. For user packs, use `user_pack_texture` from Task 5.
  - Category comes from the `sprites/<category>/` folder.
  - Missing `.wonderdraft_symbols` → defaults `radius=50`, `offset=(0,0)`, `draw_mode="normal"`.
  - Use `difflib.get_close_matches` for suggestions.
- [ ] **Step 3:** Run `pytest -v` and confirm it passes. Commit with the message `feat: asset catalogue for pck and user packs`.

### Task 7: Terrain operations

**Files:**
- Create: `src/wdmcp/ops/terrain.py`, `src/wdmcp/ops/noise.py`, `tests/test_terrain.py`

**Interfaces:**
- Consumes: `MapDocument.raster`, `raster_spec.*`
- Produces:
  - `paint_land(doc, polygon: list[tuple], mode: Literal["add","subtract"] = "add", roughness: float = 0.5, seed: int = 0) -> int`: returns the pixels changed. The polygon edge is displaced by fractal noise scaled by `roughness`. Heights rise toward the interior.
  - `generate_landmass(doc, coverage: float, seed: int, kind: Literal["continent","archipelago","island"]) -> int`
  - `raise_area(doc, polygon, amount: float) -> int`
  - `add_lake(doc, polygon, roughness=0.3, seed=0) -> int`: freshwater lake. Rasterise the noise-displaced polygon as anti-aliased coverage and write it with `raster_spec.paint_freshwater` (mask R channel, land only). Returns pixels changed. *(Updated after Spike B: lakes are mask R, not erased land.)*
  - `add_river(doc, points: list[tuple], width: float = 3.0, seed=0) -> int`: rasterise a meandering, anti-aliased polyline (width tapering toward the first point, like Wonderdraft's source fade-in) into coverage and write it with `raster_spec.paint_freshwater`. Returns pixels changed. *(Updated after Spike B: rivers are mask R, not paths.)*
  - `paint_ground(doc, polygon, color_index: int, softness: float = 0.5) -> int`
  - `noise.fbm(shape, scale, octaves, seed) -> np.ndarray[float32]` in [0, 1], deterministic per seed

- [ ] **Step 1: Write failing tests**
  - `test_paint_land_inside_is_land`: the polygon centre is land per `raster_spec`, a far corner is water
  - `test_subtract_restores_water`
  - `test_deterministic_seed`: same seed gives an identical raster hash; a different seed gives a different one
  - `test_river_follows_points`: every input point lies within `width` of the river
  - `test_terrain_roundtrip_saves`: save, reopen, raster unchanged
- [ ] **Step 2:** Run the tests and confirm they FAIL. Implement with NumPy only (polygon fill with Pillow `ImageDraw` into a mask array). Run the tests and confirm they PASS.
- [ ] **Step 3: [GUI]** Paint a test continent, open it with `wd_launch.ps1`, and check that the coastline renders in Wonderdraft. Note the result in `docs/format.md`.
- [ ] **Step 4:** Commit with the message `feat: terrain painting ops`.

### Task 8: Placement operations

**Files:**
- Create: `src/wdmcp/ops/place.py`, `src/wdmcp/ops/scatter.py`, `tests/test_place.py`

**Interfaces:**
- Consumes: `MapDocument.add/list/remove`, `template.*_entry`, `Catalog.get_set`, `points.format_points`, `raster_spec.land_alpha`, `SEA_LEVEL_ALPHA`
- Produces:
  - `add_symbol(doc, cat, set_key, pos, variant: int | None = None, scale=1.0, rotation=0.0, mirror=False, seed=0) -> str`. The `type` comes from the category: mountains→`mountain`, trees→`tree`, else `symbol`.
  - `scatter(doc, cat, set_key, polygon, density: float, min_spacing: float | None = None, land_only=True, seed=0) -> tuple[list[str], int]`: returns (ids, dropped). Poisson-disk sampling with spacing defaulting to `radius*0.9`. Symbols sort by `position.y` so lower ones draw in front.
  - `mountain_range(doc, cat, set_key, polyline, width: float, density: float, seed=0) -> list[str]`
  - `forest(doc, cat, set_key, polygon, density, seed=0) -> list[str]`
  - `add_settlement(doc, cat, name, pos, kind: Literal["city","town","village","castle","fort","ruin","port","temple","mine"], set_key: str | None = None, label=True) -> list[str]`: adds the symbol plus a label below it using theme preset City for `city`, Town for everything else
  - `add_label(doc, text, pos, preset: str = "Town", size=None, rotation=0.0, curve=0.0, color=None) -> str`
  - `add_path(doc, points, style="path_blended", width=4.0, color=None, roughness=0.33, seed=0) -> str`
  - `add_territory(doc, points, color, opacity=0.25, style="border_dash", width=10.0, smoothing=0.2) -> str`

- [ ] **Step 1: Write failing tests**
  - `test_add_symbol_entry_shape`: keys equal the fixture's symbol keys
  - `test_scatter_density_and_spacing`: no two points closer than `min_spacing`
  - `test_scatter_land_only`
  - `test_scatter_clips_out_of_bounds`: a polygon half outside the map gives every position in bounds and `dropped > 0`
  - `test_settlement_adds_symbol_and_label`
  - `test_path_points_string`
  - `test_label_uses_preset_font`
- [ ] **Step 2:** Run the tests and confirm they FAIL. Implement, then run them and confirm they PASS.
- [ ] **Step 3: [GUI]** Open a map with one of each kind in Wonderdraft and check that they all render and stay editable. Note the result.
- [ ] **Step 4:** Commit with the message `feat: symbol, label, path and territory placement`.

### Task 9: Preview renderer

**Files:**
- Create: `src/wdmcp/render/preview.py`, `tests/test_preview.py`

**Interfaces:**
- Consumes: `MapDocument`, `Catalog.image`, `raster_spec`, `points.parse_points`
- Produces: `render_preview(doc, cat, scale: float = 0.5, bbox: tuple[int, int, int, int] | None = None, show_ids: bool = False) -> PIL.Image.Image`

Drawing order:
1. water, using the theme `water_colors[0]`
2. land, coloured by height from `ground_colors` and blended with the `ground` raster
3. coastline stroke, using `landmass_outline_color`
4. freshwater
5. territories (fill at `opacity`, then the outline)
6. paths
7. symbols, sorted by z then y; tinted when `draw_mode == "sample_color"`; mirror, rotation and scale applied; anchored at `position + offset`
8. labels, drawn with a Pillow default font scaled to `size`; when `show_ids`, each object gets a small id tag

- [ ] **Step 1: Write failing tests**
  - `test_preview_size`: the fixture at scale 0.5 renders as 960×540
  - `test_preview_bbox_crop`
  - `test_preview_draws_symbol`: on a new map with one symbol, the pixel at its position differs from the background
  - `test_preview_unknown_texture_draws_placeholder`: a magenta square, with no exception
- [ ] **Step 2:** Run the tests and confirm they FAIL. Implement, then run them and confirm they PASS.
- [ ] **Step 3:** Render the fixture to `out/testmap_preview.png`. Look at it, and compare it side by side with a Wonderdraft screenshot of the same map. Note obvious mismatches in `docs/format.md`, and fix any that break readability.
- [ ] **Step 4:** Commit with the message `feat: preview renderer`.

### Task 10: MCP server (edit tools)

**Files:**
- Create: `src/wdmcp/server.py`, `src/wdmcp/session.py`, `tests/test_server.py`
- Modify: `README.md` (install and register instructions)

**Interfaces:**
- Consumes: everything above
- Produces: FastMCP app `mcp = FastMCP("wonderdraft")` and `main()`. `Session` holds `current: MapDocument | None` and a lazy `Catalog`. Tools (each mutating tool returns `{"ids": [...], "summary": str}`; every `render_preview` returns MCP `Image` content plus a saved PNG path):
  - Map lifecycle:
    - `new_map(width: int = 1920, height: int = 1080, theme: str = "Adventure")`
    - `open_map(path: str)`
    - `save_map(path: str | None = None)`
    - `map_summary()`
  - Discovery:
    - `list_themes()`
    - `list_asset_sets(category: str | None = None, query: str | None = None, limit: int = 50)`
    - `list_objects(kind: str, bbox: list[int] | None = None)`
  - Terrain: `paint_land`, `generate_landmass`, `raise_area`, `add_lake`, `add_river`, `paint_ground` (same parameters as Task 7)
  - Placement: `add_symbol`, `scatter_symbols`, `add_mountain_range`, `add_forest`, `add_settlement`, `add_label`, `add_path`, `add_territory` (same parameters as Task 8)
  - Editing:
    - `update_object(id: str, fields: dict)`
    - `move_objects(ids: list[str], dx: float, dy: float)`
    - `remove_objects(ids: list[str])`
    - `apply_theme(name: str)`
    - `set_frame(enabled: bool, texture: str | None, size: float | None)`
  - Output:
    - `render_preview(scale: float = 0.5, bbox: list[int] | None = None, show_ids: bool = False)`
    - `undo()`: snapshots before each mutation, 20 deep

- [ ] **Step 1: Write failing tests.** Call the tool functions directly, not over stdio:
  - `test_tool_flow`: new_map → generate_landmass → add_settlement → render_preview → save_map → reopen; ids are consistent
  - `test_no_map_open_error`: calling `add_label` before `new_map` raises a clear error
  - `test_undo`
- [ ] **Step 2:** Run the tests and confirm they FAIL. Implement, then run them and confirm they PASS.
- [ ] **Step 3:** Register the server with `claude mcp add wonderdraft -- <repo>\.venv\Scripts\wdmcp.exe`. In a fresh Claude Code session, run the tool flow by hand and check that the preview image shows up inline.
- [ ] **Step 4:** Commit with the message `feat: MCP server with edit and preview tools`.

### Task 11: RegionSpec and build_region

**Files:**
- Create: `src/wdmcp/region.py`, `tests/test_region.py`, `examples/sample_region.json`
- Modify: `src/wdmcp/server.py` (add tools `build_region(spec: RegionSpec, out_path: str)` and `region_spec_schema()`)

**Interfaces:**
- Produces: pydantic models.
  - `RegionSpec(name, width=1920, height=1080, theme="Adventure", seed=0, scale: Literal["world","region"], land: list[LandShape], lakes: list[Area], rivers: list[River], mountains: list[MountainRange], forests: list[Forest], settlements: list[Settlement], roads: list[Road], realms: list[Realm], labels: list[FreeLabel], frame: bool = True)`
  - `LandShape(polygon | None, generate: Literal["continent","archipelago","island"] | None, roughness)`
  - `Settlement(name, kind, pos, set_key=None)`
  - `Realm(name, polygon, color)`: becomes a territory plus a Region-preset label at the polygon's centroid
  - Others mirror the Task 7 and Task 8 parameters.
  - `build_region(spec, cat) -> MapDocument` applies the steps in the Quick Guide's order: land → mountains → rivers and lakes → ground → forests → settlements → roads → realms → labels.
  - Default asset sets per `scale` are chosen from the catalogue and listed in `region.DEFAULT_SETS`.

- [ ] **Step 1: Write failing tests**
  - `test_sample_region_builds`: `examples/sample_region.json` builds into a doc with counts equal to the spec's
  - `test_settlement_on_water_is_moved_or_reported`: the result summary lists any settlement whose position is water
  - `test_schema_exported`: `region_spec_schema()` returns valid JSON Schema
- [ ] **Step 2:** Run the tests and confirm they FAIL. Implement, then run them and confirm they PASS.
- [ ] **Step 3:** Commit with the message `feat: RegionSpec and one-shot build_region`.

### Task 12: Spike C [GUI] and the export tool

**Files:**
- Create: `src/wdmcp/export/wonderdraft_app.py`, `tests/test_export.py`
- Modify: `src/wdmcp/server.py` (add the tool `export_image(path: str, format: Literal["png","jpg"] = "png", via: Literal["wonderdraft","preview"] = "wonderdraft")`)

- [ ] **Step 1: Spike.**
  - Launch Wonderdraft with the map, using the command-line open if Spike A confirmed it, otherwise Ctrl+O.
  - Send Ctrl+E with `pywinauto.keyboard`, take a screenshot, then work out the keystroke sequence that sets the filename and confirms in the Godot-drawn dialog.
  - Note the sequence and the timings in `docs/format.md` "Spike C".
- [ ] **Step 2:** If the sequence is reliable over 3 runs, implement `export_via_app(map_path, out_path, timeout=120) -> Path`. It polls for the output file and closes Wonderdraft afterwards. If it is not reliable, `via="wonderdraft"` returns instructions for the user (open the map, press Ctrl+E) and `via="preview"` writes a full-scale preview.
- [ ] **Step 3:** Test `test_export_preview_fallback` (no GUI). The GUI path gets a manual check that is noted in `docs/format.md`.
- [ ] **Step 4:** Commit with the message `feat: image export`.

### Task 13: End-to-end acceptance on the user's campaign notes

- [ ] **Step 1:** In a fresh Claude Code session with the server attached, ask: *"Build me a region map of the Dreifurt region from my New Campaign notes."* Claude reads the vault pages (Dreifurt Region index, district and town pages), writes a `RegionSpec`, calls `build_region`, then iterates at least twice with edit tools and `render_preview`.
- [ ] **Step 2: [GUI]** Open the saved map in Wonderdraft. Check that land, rivers, mountains, forests, settlements, roads, realms and labels are present and editable. Export a PNG.
- [ ] **Step 3:** Write down any gaps as follow-up items in `docs/format.md` "Known gaps". Commit with the message `test: end-to-end acceptance on Dreifurt`.
