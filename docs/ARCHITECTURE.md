# LightBrain Architecture

## Design Philosophy

LightBrain processes audio signals and produces DMX lighting commands. It is
not a generic sound-active script. The engine separates musical content into
**lighting stems** (low/mid/high/energy) and interprets them through
mode-specific behavior profiles, palettes, and scene presets.

**Core principle: one Fixture State Model feeds all outputs.** Whether the
source is live audio, synthetic simulation, or a pre-analyzed song program,
the downstream rendering pipeline stays the same.

---

## Current Architecture

```
INPUT
  AudioCapture (sounddevice ring-buffer)    SyntheticAudioSource (--demo)
        │                                           │
        └────────────────┬──────────────────────────┘
                         │
                   AudioAnalyzer
               (FFT bands: low/mid/high/overall,
                adaptive gain normalization per band)
                         │
                   BeatDetector
               (onset threshold → BPM estimate,
                min interval 333ms → max 180 BPM,
                last-8-beats rolling average)
                         │
                   LaneSmoother
               (EMA envelope followers:
                Impact 10ms attack / 250ms decay
                Room   600ms / 3000ms
                + WAU white/amber/UV channels)
                         │
              ┌──────────┴──────────┐
              │                     │
           RoomLane            StrobeEngine
     (mode brightness          (EDM lift strobe
      profile + palette         2–16 Hz ramp on
      hold/transition SM,       high energy;
      bass-breathing pulse,     gated by mode's
      hue crossfade on switch)  strobe_allowed flag)
              │                     │
              └──────────┬──────────┘
                         │
                   SafetyEngine
               (priority chain, highest → lowest:
                1. blackout_active  — hard zero, all output
                2. strobe_allowed   — gates auto strobe per mode
                3. master_dimmer    — 0–1 overall scale
                4. uplight_dimmer   — 0–1 uplight-only scale
                5. scene overrides  — per-group brightness/color)
                         │
        ┌────────────────┼────────────────────────────┐
        │                │                            │
   Fixtures         SceneLayout                 HybridEngine
 (DMX mappers)    (RigVisualState)           (blends saved
        │                │                    program + live)
        ▼                ▼
  DMXUniverse       Visualizer
  (512-ch buffer)   (pygame 2D)
        │
  ┌─────┼──────────────────┐
  │     │                  │
Mock  Enttec            Art-Net
 DMX  USB Pro           UDP

        ┌────────────────────────────────────────────────┐
        │              WEB LAYER                         │
        │                                                │
        │  WebServer (FastAPI, port 8765)                │
        │    GET  /              dashboard.html          │
        │    GET  /api/state     JSON snapshot           │
        │    WS   /ws            live state at ~10 fps   │
        │    POST /api/command   control commands        │
        │    CRUD /api/scenes    scene editor            │
        │    GET  /api/presets   position/state presets  │
        │                                                │
        │  iPad Server (FastAPI, port 8080)              │
        │    GET  /              ipad.html PWA           │
        │    WS   /ws            bidirectional           │
        └────────────────────────────────────────────────┘
                         ▲
                  _command_queue
              (thread-safe queue.Queue;
               drained by main loop each frame)
```

---

## Command flow (web/iPad → engine)

Commands arrive from any web client and are queued into a shared
`queue.Queue`. The main render loop drains the queue once per frame via
`_web.get_all_commands()` before the audio/lighting render step.

Supported command types (all arrive as JSON):

| Type | From | Effect |
|------|------|--------|
| `mode` | dashboard | Switch lighting mode |
| `set_mode` | iPad | Switch lighting mode |
| `scene` | dashboard | Activate scene preset |
| `activate_scene` | iPad | Activate scene preset |
| `release_scene` | both | Return to active mode |
| `blackout` | both | Toggle blackout (0.8s fade-out) |
| `strobe_master` | dashboard | Set 0–1 strobe master level |
| `set_fader` | iPad | Set master / uplight / strobe fader |
| `momentary` | both | flash / strobe_burst / strobe_hold |
| `toggle_kill` | both | Toggle strobe / derby / laser kill |
| `fixture_test` | iPad | Lock all fixtures to test pattern |
| `release_fixture_test` | iPad | Return to live engine |
| `fixture_test_aim` | iPad | Set moving head pan/tilt |
| `aim_fixture` | iPad | Live joystick aim |
| `save_position` | iPad | Save named pan/tilt preset |

---

## Engine state broadcast (engine → web/iPad)

`_web.update_state(**kwargs)` is called once per frame and merges into a
shared dict. The WebSocket broadcast loop sends this dict to all connected
clients at ~10 fps. Key fields:

| Field | Type | Description |
|-------|------|-------------|
| `mode` | str | Active mode key |
| `mode_display` | str | Human-readable mode name |
| `scene` | str\|null | Active scene ID |
| `bpm` | float | Estimated BPM (0 if unknown) |
| `beat` | bool | True on beat frame (one frame only) |
| `blackout` | bool | Blackout active |
| `strobe_rate` | float | Effective strobe 0–1 (post all overrides) |
| `strobe_master` | float | Strobe master fader 0–1 |
| `master_dimmer` | float | Master brightness 0–1 |
| `uplight_dimmer` | float | Uplight brightness 0–1 |
| `low/mid/high/overall_energy` | float | Smoothed band energies |
| `impact_lane` | float | Impact lane 0–1 |
| `room_lane` | float | Room lane 0–1 |
| `fixtures` | dict | Serialized RigVisualState (uplights/washes/beams/etc.) |
| `modes` | list | [{key, display_name}] — built once from engine |
| `scenes` | list | [{id, name, index}] — rebuilt after scene edits |
| `kill_strobe` | bool | Strobe kill switch active |
| `kill_derby` | bool | Derby kill switch active |
| `kill_laser` | bool | Laser kill switch active |
| `flash_active` | bool | True while flash frames are draining |
| `test_mode` | bool | Fixture test override active |
| `test_pattern` | str | Active test pattern name |

---

## Kill switch priority chain

Manual kill switches are applied as the **last step** before fixture write,
after all other strobe/color logic:

```
auto strobe (StrobeEngine × strobe_allowed × strobe_master)
    ↓ override by strobe_burst (DJ manual, blackout-only gate)
    ↓ override by strobe_hold  (DJ manual, blackout-only gate)
    ↓ zeroed if kill_strobe = True                     ← last word
    → _eff_strobe passed to render_to_universe(strobe=...)

fixtures with set_derby_enabled() → called on toggle_kill("derby")
fixtures with enable_laser()      → called on toggle_kill("laser")
```

Blackout is separate and higher priority than kill switches — when
`blackout_active` and the fade is complete, `universe.blackout()` zeroes
all 512 channels regardless of everything else.

---

## Blackout fade

When blackout is activated:
1. `safety.state.blackout_active` is set immediately (safety wins)
2. Engine snapshots current render values (brightness, hue, white, amber, uv)
3. `_blackout_fading = True` — `universe.blackout()` is skipped
4. Each frame, render values are alpha-blended toward zero over `BLACKOUT_FADE_S = 0.8s`
5. When alpha reaches 0, `_blackout_fading = False` — hard blackout resumes

Deactivating blackout clears `_blackout_fading` immediately and resumes
normal rendering on the next frame.

---

## Fixture mapper interface

Every fixture implements `FixtureBase` and provides:

```python
def render_to_universe(
    self,
    universe: DMXUniverse,
    brightness: float,   # 0–1 master dimmer
    hue: float,          # 0–360
    saturation: float,   # 0–1
    value: float,        # 0–1 room energy
    strobe: float,       # 0–1 strobe rate
    white: float,        # 0–1 W channel
    amber: float,        # 0–1 A channel
    uv: float,           # 0–1 UV channel
    gamma: float = 2.2,
) -> None
```

The main render loop calls this with identical parameters for all fixtures.
Fixture-specific logic (sub-sections like derby, laser, spot head) is
handled internally.

Fixtures with moving heads additionally expose:
- `set_spot_aim(pan_deg, tilt_dmx)` — repositions the moving head
- `set_derby_enabled(enabled)` — kills/restores derby section
- `enable_laser(enabled)` — kills/restores laser section

---

## Song preview and saved programs

Three behavior levels are supported:

| Level | Description |
|-------|-------------|
| **Live Reactive** | Real-time audio → real-time lighting. Responds to DJ cuts and scratches. Non-deterministic by design. |
| **Deterministic Preview** | Audio file → `OfflineAnalyzer` → `DeterministicEngine` (seeded, fixed clock). Same file + mode + settings + seed = identical result every run. |
| **Saved Program** | Generated lighting pass saved as a `LightingProgram`. Exact replay or hybrid blend with live audio via `HybridEngine`. |

The visualizer and DMX renderer are fully decoupled from the source — they
consume `RigVisualState` / `FixtureStateTimeline` regardless of whether the
input is live, preview, or saved.

Determinism requires:
1. Fixed analysis (same file + FFT settings)
2. Fixed settings snapshot (mode, palette, intensity, rig config)
3. Fixed random seed (sparkle, stochastic effects use `np.random.default_rng(seed)`)
4. Fixed clock (time-dependent components accept injectable `now` parameter)

---

## Module table

### `audio/`

| Module | Responsibility |
|--------|---------------|
| `input.py` | Non-blocking sounddevice capture, ring buffer |
| `analyzer.py` | Real-time FFT band energy, adaptive gain normalization |
| `beat_detector.py` | Onset detection, BPM from last-8-beat rolling average |
| `synthetic.py` | Deterministic synthetic audio source for demo/testing |
| `offline_analyzer.py` | Batch FFT + onset/phrase/drop detection for song preview |
| `file_loader.py` | WAV/FLAC/MP3 → numpy array via soundfile |

### `engine/`

| Module | Responsibility |
|--------|---------------|
| `smoothing.py` | EMA EnvelopeFollower + LaneSmoother (impact/room/WAU) |
| `palettes.py` | HSV palette loading, shortest-path hue lerp, hold/transition SM |
| `lanes.py` | RoomLane (mode profile + palette + safety) |
| `modes.py` | 6 mode definitions (brightness/pulse/saturation/hold/strobe_allowed) |
| `safety.py` | Blackout gate, mode intensity scale, master dimmer |
| `gamma.py` | Gamma correction (v^2.2, DMX byte scaling) |
| `strobe.py` | EDM lift strobe (frequency ramp 2–16 Hz, duty cycle, hold) |
| `hue_crossfader.py` | Smooth hue crossfade when switching modes |
| `transitions.py` | Mode transition controller |
| `scenes.py` | SceneManager: load/activate/release scene presets |
| `deterministic.py` | Seeded replay engine (fixed clock + RNG, song preview) |
| `settings_snapshot.py` | Captures generation parameters for deterministic replay |

### `fixtures/`

| Module | Fixture | Channels | Notes |
|--------|---------|----------|-------|
| `rockwedge.py` | RockWedge | 8 | Current hardware placeholder |
| `chauvet_wash_fx2.py` | Chauvet Wash FX2 | 8 | 3/4/8/28Ch modes |
| `chauvet_gigbar_move_ils.py` | Chauvet GigBAR Move+ILS | 29 | Par+Derby+Flash+Laser+Spot |
| `djflx_beam.py` | DJFLX Beam | 10 | Pan/tilt moving beam |

### `dmx/`

| Module | Output type |
|--------|-------------|
| `output_mock.py` | Mock (logs changes, no hardware) |
| `output_enttec_pro.py` | Enttec USB Pro serial protocol |
| `output_artnet.py` | Art-Net 4 UDP unicast/broadcast |

### `app/render/`

| Module | Responsibility |
|--------|---------------|
| `fixture_state.py` | RigVisualState + FixtureStateTimeline dataclasses |
| `scene.py` | Maps engine output → RigVisualState for all fixture types |
| `visualizer.py` | Pygame 2D: glow, wash, beams, sparkle, HUD, blackout |
| `playback.py` | FixtureStateTimeline player (song preview) |
| `waveform.py` | Waveform + energy lane display (song preview) |

### `app/web/`

| Module | Responsibility |
|--------|---------------|
| `server.py` | FastAPI app, WebSocket manager, REST endpoints, shared state dict |
| `ipad_server.py` | iPad PWA server (port 8080), bidirectional WebSocket |
| `dashboard.html` | Desktop web dashboard (canvas visualizer, controls) |
| `ipad.html` | iPad PWA (PERF/TEST/SETUP tabs, touch-optimized) |

### `data/`

| Module | Responsibility |
|--------|---------------|
| `lighting_program.py` | LightingProgram model (identity, source, settings, analysis, output) |
| `program_store.py` | Save/load/index programs, fingerprint matching |
| `setlist.py` | Setlist + SetlistEntry models |
| `setlist_store.py` | Setlist file I/O |

---

## Key design decisions

### EMA over beat-sync

EMA smoothing creates organic, musical lighting behavior without needing
precise beat detection. Beat-locked effects (strobe, flash) are additive
overlays on top of the continuous EMA output, not a replacement.

### Fixture State Model as the contract

The visualizer and DMX renderer are completely decoupled from the audio
analysis. Any `RigVisualState` — from live audio, song preview, or saved
program — flows through identical output code.

### Why separate hold_ms from transition_ms

`hold_ms` (from Mode) controls musical pacing of color changes.
`transition_ms` (from Palette) controls the aesthetic feel of the blend.
A Banger holds 2s then blasts through a 500ms transition; Dinner holds 8s
and blends over 3s.

### Gamma on both channels

The RockWedge hardware multiplies dimmer × color. Baking brightness into
color channels would create double-compression. Instead: Ch1 = `gamma(brightness)`,
Ch2–4 = `gamma(pure_hue at v=1.0)`, so hardware multiplication gives
correct perceptual output.

### Manual strobe bypasses mode gate

Automatic EDM strobe is gated by `safety.state.strobe_allowed` (True only
for banger and indian_latin). Manual STROBE HOLD and STROBE BURST bypass
this gate — they only respect `blackout_active`. This lets the DJ fire
manual strobe in any mode, while preventing the auto engine from strobing
during dinner or speech.
