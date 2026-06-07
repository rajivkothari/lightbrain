# LightBrain Architecture

## Design Philosophy

LightBrain processes audio signals and produces lighting commands. It is not a
generic sound-active script. The engine separates musical content into
**lighting stems** (low/mid/high/energy) and interprets them through
mode-specific behavior profiles.

The core principle: **one Fixture State Model feeds all outputs**. Whether the
source is live audio, synthetic simulation, or a future song preview, the
downstream rendering pipeline stays the same.

---

## Current Architecture (Sprint 1B)

```
INPUT
  AudioCapture (sounddevice ring-buffer)       SyntheticAudioSource (--simulate)
        │                                               │
        └────────────────────┬──────────────────────────┘
                             │
                        AudioAnalyzer
                     (FFT bands: low/mid/high/overall,
                      adaptive gain normalization)
                             │
                        LaneSmoother
                     (EMA envelope followers:
                      Impact 10ms/250ms, Room 600ms/3000ms,
                      Floor/Beam/Sparkle ready Sprint 2)
                             │
              ┌──────────────┴────────────────┐
              │                               │
           RoomLane                      (future lanes)
     (mode brightness profile,
      palette hold/transition SM,
      bass-breathing pulse,
      saturation scale)
              │
         SafetyEngine
     (blackout gate, mode intensity
      scale, master dimmer)
              │
         ┌────┴──────────────────────────────────┐
         │                                       │
    RockWedge                             SceneLayout
  (HSV→RGB→gamma→DMX)                (maps engine output →
         │                             RigVisualState for
   DMXUniverse                         18 uplights + washes
  (512-ch buffer)                      + beams + sparkle)
         │                                       │
   MockDMXOutput /                       Visualizer
   EnttecProOutput                      (pygame 2D render)
  (terminal overlay)
```

---

## Target Architecture: Two Input Paths

To support Song Preview Mode (Sprint 3), the architecture adds a second input
path that converges into the same Fixture State Model:

```
┌──────────────────────────────────────────────────────────────────────────┐
│                            INPUT LAYER                                   │
│                                                                          │
│  LIVE PATH                              SONG PREVIEW PATH (future)       │
│                                                                          │
│  AudioCapture                           AudioFileLoader                  │
│  (sounddevice ring-buffer)              (WAV/MP3/FLAC batch read)       │
│  SyntheticAudioSource                   (librosa or scipy)               │
│       │                                       │                          │
└───────┼───────────────────────────────────────┼──────────────────────────┘
        │                                       │
        v                                       v
┌──────────────────────────────────────────────────────────────────────────┐
│                           ANALYSIS LAYER                                 │
│                                                                          │
│  AudioAnalyzer                          OfflineAnalyzer (future)         │
│  (real-time FFT per block)              (batch FFT + onset detection     │
│                                          + phrase/drop/silence markers)  │
│       │                                       │                          │
│       │                                 AnalysisTimeline                 │
│       │                                 (timestamped band energies,      │
│       │                                  detected events, phrase labels) │
└───────┼───────────────────────────────────────┼──────────────────────────┘
        │                                       │
        v                                       v
┌──────────────────────────────────────────────────────────────────────────┐
│                         ENGINE LAYER                                     │
│                                                                          │
│  LaneSmoother + Mode/Palette/Lane       DeterministicEngine (future)     │
│  (real-time EMA, live decisions)        (seeded; replays AnalysisTimeline│
│                                          through same smoothers with     │
│                                          fixed settings snapshot)        │
│       │                                       │                          │
└───────┼───────────────────────────────────────┼──────────────────────────┘
        │                                       │
        └─────────────────┬─────────────────────┘
                          │
                          v
┌──────────────────────────────────────────────────────────────────────────┐
│                     FIXTURE STATE MODEL                                  │
│                                                                          │
│  RigVisualState  — per-frame snapshot (current)                          │
│  FixtureStateTimeline — ordered timed frames (future Song Preview)       │
└────────────────────────┬─────────────────────────────────────────────────┘
                         │
                         v
┌──────────────────────────────────────────────────────────────────────────┐
│                       OUTPUT LAYER                                       │
│                                                                          │
│  DMX Renderer                           Preview Visualizer (pygame)      │
│  (RockWedge → DMXUniverse               (live, simulated, song preview,  │
│   → MockDMX / EnttecPro)                 saved program playback)         │
└──────────────────────────────────────────────────────────────────────────┘
```

### Key design rule

The visualizer and DMX renderer never know whether their input came from the
live path or the song preview path. They only consume `RigVisualState` /
`FixtureStateTimeline`. This keeps the output layer clean and testable.

---

## Module Responsibilities

### `audio/`

| Module | Responsibility | Status |
|--------|---------------|--------|
| `input.py` | Non-blocking sounddevice capture, ring buffer | Built |
| `analyzer.py` | Real-time FFT band energy, adaptive gain | Built |
| `synthetic.py` | Deterministic synthetic source for demo/test | Built |
| `offline_analyzer.py` | Batch FFT + onset/phrase/drop detection | **Planned Sprint 3** |
| `file_loader.py` | WAV/MP3/FLAC → numpy array | **Planned Sprint 3** |

### `engine/`

| Module | Responsibility | Status |
|--------|---------------|--------|
| `smoothing.py` | EMA envelope followers (Impact, Room, Floor, Beam, Sparkle) | Built |
| `palettes.py` | HSV palette loading, shortest-path hue lerp, hold/transition SM | Built |
| `lanes.py` | RoomLane (mode profile + palette + safety) | Built |
| `modes.py` | Mode definitions with brightness/pulse/saturation/hold profiles | Built |
| `safety.py` | Blackout gate, mode scale, master dimmer | Built |
| `gamma.py` | Gamma correction (v^2.2, DMX scaling) | Built |
| `deterministic.py` | Seeded replay of AnalysisTimeline through same engine | **Planned Sprint 3** |
| `program_generator.py` | Generates LightingProgram from deterministic pass | **Planned Sprint 4** |

### `app/render/`

| Module | Responsibility | Status |
|--------|---------------|--------|
| `fixture_state.py` | Fixture state dataclasses + FixtureStateTimeline stub | Built (partial) |
| `scene.py` | Maps engine output → RigVisualState (18 uplights, 3 washes, 2 beams) | Built |
| `visualizer.py` | Pygame renderer: glow, wash, beams, sparkle, HUD, blackout | Built |
| `playback.py` | FixtureStateTimeline player with playhead, controls | **Planned Sprint 3** |
| `waveform.py` | Waveform + energy lane display in visualizer | **Planned Sprint 3** |

### `data/` (future)

| Module | Responsibility | Status |
|--------|---------------|--------|
| `lighting_program.py` | LightingProgram model (full spec in SONG_PREVIEW_MODE.md) | **Planned Sprint 4** |
| `program_store.py` | Save/load LightingProgram to JSON/SQLite | **Planned Sprint 4** |

---

## Determinism Contract

The live engine is intentionally non-deterministic: it reacts to live input,
uncontrolled timing, and floating-point accumulation over time. The same song
may look slightly different on each play.

The song preview path is designed to be deterministic:

1. **Fixed analysis**: same file + same window/hop size = same band energies
2. **Fixed settings snapshot**: mode, palette, intensity, fixture config captured at preview time
3. **Fixed random seed**: sparkle, random color variations, any stochastic effects use `np.random.default_rng(seed)`
4. **Fixed clock**: the engine's time source is replaced by the analysis timeline's timestamp, not `time.monotonic()`
5. **Seeded synthetic source**: `SyntheticAudioSource(seed_offset=N)` can be fixed for testing

Any component that calls `time.monotonic()`, `random.random()`, or equivalent
must accept an injectable clock / RNG for deterministic operation.

Files that currently use uncontrolled time and need adapting for Song Preview:
- `engine/palettes.py` — `PaletteBlender` uses `time.monotonic()` for blend timing
- `engine/smoothing.py` — `EnvelopeFollower` uses `time.monotonic()` for dt
- `app/render/scene.py` — `SceneLayout` uses `time.monotonic()` for beam animation
- `app/render/visualizer.py` — sparkle uses `time.monotonic()` via `random.Random`

---

## Key Design Decisions

### Why EMA over beat-sync?

EMA smoothing is intentionally non-quantized. It creates organic lighting
behavior that feels musical without needing precise beat detection. Beat-locked
lighting will be added as an optional overlay in Sprint 2+, not as a
replacement.

### Why Fixture State Model as the contract?

The visualizer and DMX renderer are completely decoupled from the audio
analysis. This means:
- The visualizer can display song preview frames exactly as it displays live frames
- Tests can inject any RigVisualState without audio
- Saved programs replay through the same output code path as live operation

### Why separate hold_ms from transition_ms?

`hold_ms` (from Mode) controls the musical pacing of color changes per scene.
`transition_ms` (from Palette) controls the aesthetic feel of the blend itself.
A Banger mode might hold for 2s then blast through a 500ms transition, while
Dinner holds for 8s and blends over 3s.

### Why gamma 2.2 on both dimmer and color channels?

The RockWedge hardware multiplies dimmer × color. If brightness was baked into
color channels, the effective output would be `(v·b)^2.2 × (v·b·r)^2.2`, which
is heavily double-compressed. Instead: Ch1 = gamma(brightness), Ch2-4 =
gamma(pure_hue at v=1.0), so the hardware multiplication gives correct output.
