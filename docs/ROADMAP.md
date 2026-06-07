# LightBrain Roadmap

## Phase 1 — Sprint 1 / 1B: Core Engine MVP ✅

**Status: Complete**

The foundation is working and tested (115 tests passing).

### Built

- [x] Live audio capture via sounddevice (non-blocking ring buffer)
- [x] FFT band energy analyzer (low/mid/high/overall, adaptive gain)
- [x] EMA envelope followers with separate attack/decay taus
- [x] Impact + Room lanes (Floor/Beam/Sparkle lanes ready but not wired)
- [x] HSV palettes with shortest-path hue interpolation
- [x] Palette hold/transition state machine (HOLDING → TRANSITIONING)
- [x] Per-mode brightness profiles (base/max brightness, pulse amount, saturation scale, hold_ms)
- [x] Gamma correction (v^2.2, clean dimmer/RGB separation)
- [x] 512-channel DMX universe (1-indexed, numpy uint8)
- [x] RockWedge 8-channel RGBWAUV mapper (double-dimming fix)
- [x] MockDMXOutput (terminal overlay)
- [x] EnttecProOutput (Enttec USB Pro framing, hardware-untested)
- [x] ANSI terminal overlay (FPS, bands, lanes, color names, hold timer, brightness breakdown)
- [x] Synthetic audio source (deterministic drum machine / music sim)
- [x] 2D Preview Visualizer (pygame): uplights, washes, beam cones, sparkles, impact flash, blackout
- [x] Non-blocking keyboard mode switching (O/D/B/I/S/L/Space/Q)
- [x] `--simulate` / `--demo` flag in all scripts
- [x] Fixture State Model (`RigVisualState`, `FixtureStateTimeline` stub)
- [x] Architecture docs, roadmap, Song Preview Mode planning

### Rig simulated in visualizer

- 18 RockWedge uplights (perimeter)
- 2 Chauvet Wash FX
- 1 Chauvet GigBAR (central wash + derby sparkle + impact flash)
- 2 DJFLX moving beams

---

## Phase 2 — Sprint 2: Near-Term Polish

**Status: Complete — 143 tests passing**

### Priority items

- [ ] Floor Lane wired to DMX (mid-energy → floor wash brightness)
- [ ] Beam Lane wired to DMX (mid + high → moving head intensity)
- [ ] Sparkle Lane wired to DMX (high → strobe/UV channel)
- [ ] White / Amber / UV channel control (palette-driven per mode)
- [ ] Beat detection: simple onset/threshold algorithm for palette transition triggers
- [ ] Phrase detection (rough): energy gradient over N-bar window
- [ ] Mode transition animations (smooth crossfade vs snap on mode switch)
- [ ] Individual uplight addressing (per-fixture color group support)
- [ ] MIDI controller input (CC messages → mode/dimmer/blackout)
- [ ] GigBAR Move + ILS: beam line visualization in the 2D visualizer
- [ ] Full Chauvet GigBAR DMX channel map
- [ ] Chauvet Wash FX channel map
- [ ] DJFLX channel map and movement control
- [ ] Fixture wizard (interactive DMX address/mode configurator)
- [ ] Multi-fixture group management (per-group color, intensity)

### Visualizer improvements

- [ ] Show each uplight's actual DMX address and current channel values
- [ ] Beam gobo visualization (simple pattern overlay)
- [ ] Mode transition visual indicator (flash/fade on switch)
- [ ] Palette color strip showing all colors + current position

### Hardware

- [ ] Test EnttecProOutput against physical DMXking ultraDMX MAX
- [ ] Verify RockWedge channel map against physical unit
- [ ] Test wireless DMX transmitter latency

---

## Phase 3 — Sprint 3: Song Preview MVP

**Status: Complete — 189 tests passing**

### Core deliverables

- [x] `audio/file_loader.py` — WAV/FLAC → numpy array via soundfile (scipy fallback)
- [x] `audio/offline_analyzer.py` — batch FFT, spectral-flux onset detection, phrase/drop detection, BPM estimate → `AnalysisTimeline`
- [x] `engine/settings_snapshot.py` — `SettingsSnapshot` dataclass captures all generation params
- [x] `engine/deterministic.py` — drives smoothers + lanes from `AnalysisTimeline` with injected clock + seeded RNG → `FixtureStateTimeline`
- [x] Clock injection — all time-dependent components accept `now=` parameter; deterministic engine passes `frame.time_s`
- [x] `app/render/playback.py` — `PlaybackController`: wall-clock playhead through `FixtureStateTimeline`
- [x] `app/render/waveform.py` — static surface (energy lanes, onset markers, event labels) + per-frame playhead
- [x] `app/render/fixture_state.py` — `frame_at()` upgraded to O(log n) bisect
- [x] `scripts/test_song_preview.py` — full pygame song preview runner
- [x] Visualizer keyboard: Space play/pause, ←→ ±5s, `[`/`]` phrase seek, R re-run, O/D/B/I/S/L mode switch, Q quit

### Determinism requirements

See `docs/SONG_PREVIEW_MODE.md` → Determinism Requirements section.

Short version: same file + same mode + same settings + same seed = identical preview every run.

### Dependencies to evaluate

- `librosa` — full-featured audio analysis (onset, beat tracking, spectral features, BPM). Heavy dependency (~300MB), but best in class.
- `scipy` + `soundfile` — lightweight alternative for WAV analysis. Limited onset detection.
- Decision: spike `librosa` for onset detection quality. Use `soundfile` for file loading only if librosa is too heavy.

---

## Phase 4 — Sprint 4: Saved Program Mode

**Status: Complete — 223 tests passing**

### Core deliverables

- [x] `data/lighting_program.py` — `LightingProgram` dataclass + `ProgramSummary` + `compute_song_fingerprint` (SHA-256, volume-normalized)
- [x] `data/program_store.py` — `ProgramStore`: JSON save/load, index for fast listing, find-by-fingerprint
- [x] Full serialization roundtrip: nested dataclasses → JSON → rebuilt dataclasses; RGB tuples, BPM estimate, and all fields preserved
- [x] `scripts/test_song_preview.py` — Ctrl+S save, Ctrl+L program library modal (navigate ↑↓, Enter load, Del delete)

---

## Phase 5 — Sprint 5: Hybrid Playback + Art-Net Output + Auto Song Matching

**Status: Complete — 261 tests passing**

### Delivered

- [x] `app/hybrid.py` — `HybridEngine`: blends a saved `FixtureStateTimeline` with the live audio engine output; blend=0→pure live, blend=1→pure program; `blend_rig_states()` lerps all scalar and RGB fields, handles mismatched fixture counts, propagates blackout from either source
- [x] `dmx/output_artnet.py` — Art-Net 4 ArtDmx UDP output; configurable unicast/broadcast target IP and 15-bit universe; compliant packet layout (spec §9.1); `connect()`/`disconnect()` lifecycle + context manager
- [x] Auto song matching in `scripts/test_song_preview.py` — on file load, SHA-256 fingerprint is checked against `ProgramStore`; matching program auto-loads (timelines + settings) before showing the visualizer

---

---

## Phase 6 — Sprint 6: Setlist Mode + Moving Head DMX

**Status: Complete — 298 tests passing**

### Delivered

- [x] `data/setlist.py` — `Setlist` + `SetlistEntry` + `SetlistSummary`; `add_entry`, `remove_entry`, `move_entry`, `find_by_fingerprint`, `update_entry_program`, `_renumber`; all mutations bump `updated_at`
- [x] `data/setlist_store.py` — `SetlistStore`: JSON save/load/delete/list; index stores per-setlist fingerprint lists for fast `find_by_fingerprint` without loading full data
- [x] `fixtures/djflx_beam.py` — 10-channel moving beam mapper; `BeamState.angle_degrees` → pan (±90° → 0–255 centre=128); brightness → gamma-corrected dimmer; RGB direct; movement_speed → inverted speed channel; inactive state zeros dimmer
- [x] `scripts/test_song_preview.py` — setlist auto-detection on audio load; reports "Track N/M of '<setlist>'" when song fingerprint matches a saved setlist entry

---

## Phase 13 — Sprint 13: iPad Web Controller

**Status: Complete — 422 tests passing**

### Delivered

- [x] `config/app_config.json` — new config file: `web_server_enabled`, `web_server_port` (8080), `headless_mode`
- [x] `app/web/ipad_server.py` — FastAPI server on port 8080 sharing `_engine_state` and `_command_queue` with the dashboard; pushes state at 15 Hz; bidirectional WebSocket: iPad sends commands upstream, server pushes state downstream
- [x] `app/web/ipad.html` — self-contained PWA (no CDN dependencies): dark high-contrast theme for DJ booth use; two views (Performance + Setup); large touch targets (min 60×60px); `manifest.json` for "Add to Home Screen"; WebSocket auto-reconnect with connection status indicator
- [x] `app/main.py` — `--ipad-port` and `--headless` CLI args; loads `app_config.json` for defaults; starts iPad server alongside dashboard; handles new command types: `set_mode`, `activate_scene`, `set_fader` (master/uplight/strobe), `momentary` (flash/strobe_burst), `aim_fixture`, `save_position`; `_master_dimmer` and `_uplight_dimmer` faders applied to DMX output and visualizer; `_flash_frames` for manual flash hit; `_strobe_burst_end` for timed strobe burst; headless mode disables terminal overlay and keyboard thread
- [x] `app/web/server.py` — added `master_dimmer` and `uplight_dimmer` to `_engine_state` defaults

### iPad PWA features

- **Performance view**: energy meters (Low/Mid/High/Overall), mode buttons (2-col grid), scene presets (2-col grid + Release), faders (Master/Uplight/Strobe with large thumb sliders), momentary buttons (Flash Hit / Strobe Burst), blackout button (sticky bottom, always accessible)
- **Setup view**: system status (DMX output, engine FPS, connection), fixture aiming joystick (pan 0–540°, tilt 0–270°), position save flow
- **PWA**: `apple-mobile-web-app-capable`, standalone display mode, dark status bar, safe-area insets for modern iPads

### Command protocol (WebSocket bidirectional)

```
iPad → Server:
  {type: "set_mode",        value: "banger"}
  {type: "activate_scene",  value: "first_dance"}
  {type: "blackout"}
  {type: "set_fader",       fader: "master",  value: 0.8}
  {type: "momentary",       effect: "flash",  action: "start"}
  {type: "aim_fixture",     fixture: "beam_l", pan: 270, tilt: 135}
  {type: "save_position",   name: "center",   fixture: "beam_l", ...}

Server → iPad (15 Hz):
  Full engine state dict including mode, scene, energy bands,
  BPM, fader values, fixtures, DMX output type, FPS
```

### Headless mode

```
python -m app.main --demo --headless
# No terminal overlay, no keyboard input
# iPad on port 8080 is the only UI
# Dashboard still available on port 8765 with --web
```

---

## Phase 12 — Sprint 12: Strobe Master + Lighting Lanes

**Status: Complete — 414 tests passing**

### Delivered

- [x] `engine/strobe.py` — raised `_THRESHOLD` from 0.28 to 0.55 so strobe only fires during actual EDM build-ups
- [x] `app/web/server.py` — added `impact_lane`, `room_lane`, `strobe_rate`, `strobe_master` to `_engine_state`
- [x] `app/main.py` — `_strobe_master` slider; reads `strobe_master` command; applies as multiplier on strobe rate; pushes impact/room lanes and strobe values in state
- [x] `app/web/dashboard.html` — "Audio Bands" and "Lighting Lanes" sections with labelled bars (Impact→flashes, Room→color, Strobe→EDM); strobe master slider (0–100%); strobe bar flashes white when firing

---

## Phase 11 — Sprint 11: Live Visual Quality

**Status: Complete — 414 tests passing**

### Delivered

- [x] `engine/hue_crossfader.py` — `HueCrossfader`: captures departing hue on mode switch and blends to the new hue over 0.5s using shortest-path interpolation; `instant=True` skips crossfade for snap modes (banger)
- [x] `app/render/scene.py` — per-fixture color zones: 4 wall segments (top/bottom/left/right) each get mode-specific hue offsets; `_ZONE_OFFSET` dict drives offsets from 0° (speech) to 50° (banger); `ambient_white`/`ambient_amber` params feed `RigVisualState.ambient_warm`
- [x] `app/render/fixture_state.py` — `RigVisualState.ambient_warm: float` field carries the blended W/A channel level to the canvas
- [x] `app/main.py` — `HueCrossfader` wired to mode-change detection; `_last_hue` tracking; `ambient_white`/`ambient_amber` passed to `scene_layout.update_and_build()`
- [x] `app/web/server.py` — `serialize_rig_state()` includes `ambient_warm`
- [x] `app/web/dashboard.html` — amber floor-wash overlay (`rgba(255,148,24,…)`) drawn over dance floor when `ambient_warm > 0.04` (visible during dinner/slow_dance/indian_latin modes)

### What these fix

- **Color zones**: all 18 uplights previously shared one hue; now top/bottom walls contrast and sides blend between them — most visible in banger (±50°) and open_dance (±35°)
- **Warm canvas tint**: White/Amber DMX channels drove physical hardware warmth but were completely invisible in the visualizer; now dinner/slow_dance modes show an amber floor wash
- **Hue crossfade**: mode switches previously caused an instant palette hue jump; now the display hue blends smoothly over 0.5s (DMX output unaffected — hardware transitions are already handled by the palette hold/blend cycle)
- **Beat-sync note**: already fully wired in earlier sprints (banger=`fast_beat`, open_dance/indian_latin=`energy_trigger`); no changes needed

---

## Phase 10 — Sprint 10: EDM Lift Strobe

**Status: Complete — 399 tests passing**

### Delivered

- [x] `engine/strobe.py` — `StrobeEngine`: time-based oscillator that activates during EDM energy rises; ramps from 2Hz→16Hz as `high_energy` climbs above 0.28 threshold; 25% duty cycle; 0.22s hold-off after energy drops; `update()` returns `(strobe_on: bool, rate: float, freq: float)`
- [x] `engine/modes.py` — enabled `allow_strobe=True` for `banger` and `indian_latin` modes
- [x] `engine/safety.py` — `update_from_mode()` now propagates `mode.allow_strobe` to `safety.state.strobe_allowed`; `apply()` passes strobe through the safety chain (blackout still forces to 0)
- [x] `fixtures/rockwedge.py` — Ch8 strobe DMX now active: `(11 + int(strobe * 244)) if strobe > 0 else 0` (range: 0=off, 11=slowest, 255=fastest per RockWedge datasheet)
- [x] `app/render/scene.py` — `update_and_build()` accepts `strobe_on` and `strobe_rate`; GigBAR impact flash triggers on `impact > 0.75 OR strobe_on`; flash brightness uses `max(impact, strobe_rate * 0.65)` during strobe
- [x] `app/main.py` — `StrobeEngine` wired into per-frame loop; `strobe_on`/`strobe_rate` fed to safety chain and scene layout; RockWedge DMX write uses live strobe value
- [x] `app/web/dashboard.html` — full-screen white flash on GigBAR impact (opacity `brt*0.28`) visible in canvas visualizer during strobe events; GigBAR movers added as actual beam cones (`gigbar_mover_l`, `gigbar_mover_r`)

### Strobe behavior

- Activates only in `banger` and `indian_latin` modes when `high_energy ≥ 0.28`
- Frequency ramps linearly: 2Hz at threshold → 16Hz at full energy
- 25% duty cycle (on for 25% of each strobe period)
- 0.22s hold keeps strobe alive briefly after energy drops (avoids flicker on transients)
- dt capped at 0.1s per update to prevent large time jumps breaking the oscillator

---

## Phase 9 — Sprint 9: Canvas Visualizer + Scene Editor

**Status: Complete — 378 tests passing**

### Delivered

- [x] `app/web/server.py` — added `GET /api/scenes`, `POST /api/scenes`, `DELETE /api/scenes/{id}`, `GET /api/presets` endpoints; `set_paths()` to configure file paths and scene manager; `serialize_rig_state()` to convert RigVisualState to JSON; `_refresh_scene_catalog()` to rebuild scene list after edits
- [x] `app/web/dashboard.html` — full rewrite: tab system (Live / Scene Editor); 2D canvas rig visualizer with beam cones, wash glows, uplight halos, sparkle particles, blackout flash; responsive via ResizeObserver; scene editor panel with scene list, form editor (scene_id, name, base mode, fixture groups with state/position presets), save/delete via REST API
- [x] `app/main.py` — added `SceneLayout` instance; builds per-frame `RigVisualState` via `scene_layout.update_and_build()`; applies scene overrides via `scene_mgr.apply_to_rig_state()`; pushes serialized fixture state to web via `_web.update_state(fixtures=...)`; calls `_web.set_paths()` at startup to wire scene CRUD API

### Usage

```
python -m app.main --demo --web
# Open http://localhost:8765/ in any browser
# Live tab: 2D rig visualizer updates at 10fps
# Scene Editor tab: create/edit/delete/activate scenes in-browser
```

---

## Phase 8 — Sprint 8: Web Dashboard

**Status: Complete — 359 tests passing**

### Delivered

- [x] `app/web/server.py` — FastAPI server with `GET /` (dashboard HTML), `GET /api/state` (JSON snapshot), `POST /api/command` (mode/scene/blackout control), `WS /ws` (live state push at ~10 fps); runs in a daemon thread alongside `app.main`
- [x] `app/web/dashboard.html` — single-page dashboard: live energy bars, mode buttons, 3×3 scene grid (F1–F9), release scene, blackout toggle; WebSocket with auto-reconnect; works on phone/tablet on the same network
- [x] `app/main.py` — `--web` / `--web-port` flags; web command loop (mode, scene, release_scene, blackout); per-frame state push; fps counter
- [x] `requirements.txt` — added `fastapi>=0.110.0` and `uvicorn[standard]>=0.29.0`

### Usage

```
python -m app.main --demo --web
# Open http://localhost:8765/ in any browser
```

---

## Phase 7 — Sprint 7: Preset System

**Status: Complete — 344 tests passing**

### Delivered

- [x] `fixtures/positions.json` — 8 named pan/tilt positions (center, park, ceiling, left_sweep, right_sweep, cake_table, entrance, dance_floor)
- [x] `fixtures/states.json` — 10 named colour/intensity states (blush_pink, deep_blue, warm_amber, wedding_white, uv_glow, classic_red, ocean_teal, sunset_gold, party_purple, clean_white)
- [x] `config/scenes/` — 9 default wedding/event scene JSON files: first_dance, cake_cutting, toasts, bouquet_garter, grand_entrance, dinner_service, open_dancing, last_dance, send_off
- [x] `engine/scenes.py` — `SceneManager` (load_all, activate_scene, release_scene, apply_to_rig_state, get_uplight_color_override, list_scenes); `ScenePreset`, `GroupOverride`, `PositionPreset`, `StatePreset` dataclasses
- [x] `fixtures/aiming.py` — `FixtureAimingTool`: set_pan/set_tilt, nudge_pan/nudge_tilt, go_to_preset, save_position, delete_position
- [x] `app/main.py` — F-key escape sequence parsing (xterm / VT100 / Linux console); SceneManager wired; F1-F9 activate scenes 1-9, F10 releases; scene colour override applied to RockWedge HSV render path; active scene shown in terminal overlay
- [x] `scripts/test_song_preview.py` — pygame.K_F1–K_F9 activate scenes; pygame.K_F10 releases; scene_mgr.apply_to_rig_state() called on every frame before visualizer draw; scene name shown in controls bar

### Renderer priority chain (implemented)

    blackout → safety → scene overrides → MIDI momentary → MIDI continuous →
    mode engine → audio reactivity

---

## Phase 5 — Advanced Future (Remaining Backlog)

**Status: Research / Backlog**

### Intelligence

- [ ] Song fingerprint library — auto-match loaded song to saved program (basic version shipped in Sprint 5)
- [ ] Auto-detected preferred lighting profiles per song/genre
- [ ] Energy curve normalization across songs (so Banger always hits at the right level)
- [ ] Crowd/venue adaptation (ambient light sensor, crowd noise gate)

### Hardware

- [ ] Art-Net / sACN output (network lighting)
- [ ] Raspberry Pi deployment (LightBrain as standalone device)
- [ ] LightBrain Go — dedicated hardware unit concept

### Fixtures

- [ ] Moving head full control (pan/tilt + gobo + effects)
- [ ] Par can arrays (group addressing, chase patterns)
- [ ] LED strips (pixel mapping)
- [ ] Haze machine integration (auto-haze on drop)

### Platform

- [ ] Web dashboard (remote control, live monitoring)
- [ ] Mobile app (iOS/Android control surface)
- [ ] Multi-room / multi-rig networking
- [ ] Setlist integration (DJ software metadata → LightBrain mode cues)
- [ ] DJ software plugin (Serato/Traktor MIDI clock sync)

---

## Behavior Mode Summary

| Mode | Source | Determinism | Described in |
|------|--------|-------------|-------------|
| Live Reactive | Real-time audio | Non-deterministic by design | ARCHITECTURE.md |
| Simulated | SyntheticAudioSource | Deterministic given fixed seed | ARCHITECTURE.md |
| Song Preview | Offline audio file analysis | Fully deterministic (same input = same output) | SONG_PREVIEW_MODE.md |
| Saved Program | Pre-generated LightingProgram | Exact replay | SONG_PREVIEW_MODE.md |

---

## Test Coverage Goals

| Sprint | Tests | Coverage focus |
|--------|-------|---------------|
| 1 / 1B | 115 ✅ | Engine correctness, fixture state, scene layout |
| 2 | 143 ✅ | Beat detection, WAU channels, MIDI, ModeTransitioner |
| 3 | 189 ✅ | OfflineAnalyzer, DeterministicEngine, determinism contract, PlaybackController |
| 4 | 223 ✅ | LightingProgram save/load, fingerprinting, serialization roundtrip |
| 5 | 261 ✅ | HybridEngine blend, Art-Net packet structure, auto song matching |
| 6 | 298 ✅ | Setlist model/store, DJFLXBeam 10-ch pan/tilt mapper, setlist auto-detection |
| 7 | 344 ✅ | Scene presets, position/state presets, SceneManager, FixtureAimingTool, F1-F9 shortcuts |
| 8 | 359 ✅ | Web dashboard (FastAPI + WebSocket), live energy bars, mode/scene/blackout control |
| 9 | 378 ✅ | Canvas rig visualizer, scene editor UI, scene CRUD API, serialize_rig_state |
| 10 | 399 ✅ | EDM lift strobe engine, safety chain strobe passthrough, RockWedge Ch8, visual flash |
| 11 | 414 ✅ | Per-fixture color zones, mode hue crossfade, ambient warm tint in canvas |
| 12 | 414 ✅ | Strobe master slider, lighting lanes clarity (Impact/Room/Strobe bars) |
| 13 | 422 ✅ | iPad PWA controller, master/uplight faders, flash/strobe hit, headless mode |
