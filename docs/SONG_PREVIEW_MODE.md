# Song Preview Mode

## Overview

Song Preview Mode lets a DJ load an audio file into the LightBrain visualizer
and preview the likely lighting behavior for that song *before* connecting
physical fixtures.

The goal is to let a DJ answer:

- Will this song look too aggressive in Banger mode?
- Are the uplights too bright for a first-dance moment?
- Do the drops hit correctly? Does the build feel right?
- Is Indian/Latin mode appropriate for this track?
- Should strobe be disabled for this song?
- Should the palette be locked to warm colors throughout?
- Can I save this lighting pass and replay it exactly next week?

---

## Three Behavior Levels

### 1. Live Reactive Mode (current default)

The engine responds to real-time audio in performance.

```
Live audio input
→ real-time analysis (one block at a time)
→ real-time lighting decisions
→ DMX output + visualizer
```

**Characteristic:** Same song may look *similar* but not *identical* each time.
Lighting reacts to live input level, DJ cuts, loops, scratches, blend points, and
current palette/mode state. This is intentional — it keeps lighting organic and
responsive to the actual performance.

### 2. Deterministic Preview Mode

The engine runs an offline pass over a complete audio file with a fixed settings
snapshot and fixed random seed.

```
Audio file (WAV/MP3/FLAC)
→ offline analysis (full song, batch FFT)
→ AnalysisTimeline (timestamped band energies, onset markers, phrase labels)
→ DeterministicEngine (fixed clock, seeded RNG, fixed settings snapshot)
→ FixtureStateTimeline (ordered timed frames)
→ Visualizer playback (playhead, controls, waveform display)
```

**Characteristic:** Same file + same mode + same settings + same seed =
**identical preview every run.** This is critical for user trust. The DJ must be
able to say "this is exactly what my lighting will look like for this track."

### 3. Saved Program Mode

After previewing a song, the DJ saves the generated lighting pass as a
**Lighting Program**. Later, when that song plays live, LightBrain can:

- Play the saved program exactly (fully scripted)
- Use the saved program as a guide while still reacting live (hybrid)
- Allow manual override at any time (safety net)

This creates a bridge between Maestro-style intelligence and SoundSwitch-style
repeatable shows.

---

## Architecture for Song Preview

### New modules required

```
audio/
  file_loader.py         Load WAV/MP3/FLAC into a numpy float32 array
                         (scipy.io.wavfile for WAV; librosa or soundfile for MP3/FLAC)

  offline_analyzer.py    OfflineAnalyzer:
                         - batch FFT across full file (with same window/hop as live)
                         - compute low/mid/high/overall energy per frame
                         - onset detection (flux or HFC algorithm)
                         - phrase/drop detection (energy variance + silence gating)
                         - silence / speech region detection
                         → returns AnalysisTimeline

engine/
  deterministic.py       DeterministicEngine:
                         - accepts AnalysisTimeline + SettingsSnapshot + seed
                         - drives existing LaneSmoother, RoomLane, SafetyEngine
                           using AnalysisTimeline timestamps as the clock source
                           (no time.monotonic() — uses analysis frame timestamps)
                         - uses seeded np.random.default_rng(seed) for all randomness
                         → returns FixtureStateTimeline

app/render/
  playback.py            FixtureStateTimeline player:
                         - play(), pause(), seek(t), step_forward(), step_back()
                         - emits RigVisualState at the correct playback time
                         - exposes current_time_s, duration_s, is_playing

  waveform.py            Waveform + energy lane display:
                         - renders amplitude waveform as a scrolling/fixed strip
                         - renders low/mid/high/overall energy lanes
                         - marks detected onsets, phrases, drops, silence regions
                         - tracks playhead position

data/
  lighting_program.py    LightingProgram dataclass (see model below)
  program_store.py       Save/load LightingProgram to JSON or SQLite
```

### AnalysisTimeline model

```python
@dataclass
class AnalysisFrame:
    time_s: float
    low_energy: float
    mid_energy: float
    high_energy: float
    overall_energy: float
    is_onset: bool
    onset_strength: float

@dataclass
class EventMarker:
    time_s: float
    label: str          # "phrase_start", "drop", "breakdown", "silence", "speech"
    confidence: float   # 0.0–1.0

@dataclass
class AnalysisTimeline:
    frames: List[AnalysisFrame]
    events: List[EventMarker]
    duration_s: float
    sample_rate: int
    hop_size: int
    window_size: int
    bpm_estimate: Optional[float]
    key_estimate: Optional[str]
```

### SettingsSnapshot model

```python
@dataclass
class SettingsSnapshot:
    mode_key: str
    palette_key: str
    master_dimmer: float
    intensity_override: Optional[float]    # None = use mode.intensity_scale
    base_brightness_override: Optional[float]
    max_brightness_override: Optional[float]
    pulse_amount_override: Optional[float]
    saturation_scale_override: Optional[float]
    fixture_rig_config: dict               # serialized from rig_config.json
    created_at: float                      # time.time()
```

---

## LightingProgram Model

A Lighting Program is the saved output of a deterministic preview pass. It
contains everything needed to replay the lighting show exactly — or to use as
a template for hybrid live operation.

```python
@dataclass
class LightingProgram:
    # Identity
    program_id: str                        # UUID
    name: str
    version: int                           # schema version for migration

    # Source
    song_file_path: str
    song_fingerprint: str                  # SHA-256 of audio content
    song_duration_s: float

    # Settings used during generation
    settings: SettingsSnapshot
    random_seed: int

    # Analysis data (reference copy, can be regenerated)
    analysis_timeline: AnalysisTimeline

    # Generated lighting data
    fixture_state_timeline: FixtureStateTimeline

    # Metadata
    created_at: float
    updated_at: float
    notes: str
```

### Why store both analysis and fixture timeline?

- `analysis_timeline` can be regenerated from the audio file if needed (for storage savings)
- `fixture_state_timeline` is the replay-ready output — no re-analysis needed for live playback
- Including both lets the DJ adjust settings and re-run the deterministic engine quickly (just re-run `DeterministicEngine` with the stored `analysis_timeline` + new settings)

### Fingerprinting strategy

Use SHA-256 hash of the first 30 seconds of PCM audio (normalized).
This is cheap, collision-resistant for practical purposes, and doesn't require
heavy audio fingerprinting libraries.

Full acoustic fingerprinting (Chromaprint/AcoustID) is deferred to Phase 5 for
setlist auto-matching.

---

## Determinism Requirements

All the following must be fixed for a deterministic preview:

| Requirement | Current state | Change needed |
|-------------|--------------|---------------|
| Fixed analysis window/hop | `block_size=1024` in analyzer | Same constant used for offline pass |
| Fixed random seed | Not seeded (live only) | `np.random.default_rng(seed)` injected |
| Fixed mode/profile snapshot | Mode read at preview start | Captured in `SettingsSnapshot` |
| Fixed palette selection | Palette read at preview start | Captured in `SettingsSnapshot` |
| Fixed rig config | Read from `rig_config.json` | Serialized into `SettingsSnapshot` |
| Deterministic smoothing | Uses `time.monotonic()` | Replace with frame timestamps in `DeterministicEngine` |
| Deterministic beam animation | Uses `time.monotonic()` | Replace with playback time |
| Deterministic sparkle | Uses `time.monotonic()` + unseeded `random.Random` | Use `np.random.default_rng(seed)` |
| Deterministic palette blend | Uses `time.monotonic()` | Replace with frame timestamp |

### Clock injection pattern

Rather than a global clock, each time-dependent component should accept an
optional `now` parameter. During live operation, `now=None` falls through to
`time.monotonic()`. During deterministic replay, `now` is the analysis frame's
`time_s` value:

```python
# Example: EnvelopeFollower.update() with injectable clock
def update(self, raw: float, now: Optional[float] = None) -> float:
    if now is None:
        now = time.monotonic()
    dt_s = now - self._last_update
    ...
```

This is a backwards-compatible API change. Existing callers pass nothing and
behavior is unchanged. The `DeterministicEngine` passes `frame.time_s`.

Files to update for clock injection:
- `engine/smoothing.py` → `EnvelopeFollower.update(raw, now=None)`
- `engine/palettes.py` → `PaletteBlender.update(energy, now=None)`
- `app/render/scene.py` → `SceneLayout.update_and_build(..., now=None)`

---

## Visualizer Changes for Song Preview

The `Visualizer` (pygame) should eventually support:

### Playback controls strip (bottom of window)

```
[◀◀] [◀] [▶/⏸] [▶] [▶▶]   ──────●───────────────────  01:23 / 03:45
```

### Waveform + energy lane display (below dance floor)

```
┌─────────────────────────────────────────────────────────────────┐
│ WAVEFORM  ~│~~~~│~~~~~~│~~~~│~│~~~~│~~~~~~~~│~~~~~~~~~~~~~~~~~~~~│
│ LOW        ████░░░██████░░░░░░██████████░░░░░░░░░░░░░░░░░░░░░░░ │
│ MID        ██░░░████░░░████░░░░░██████░░░░░░░░░░░░░░░░░░░░░░░░░ │
│ HIGH       █░░████░░░░████░░░░░░███░░░░░░░░░░░░░░░░░░░░░░░░░░░░ │
│ ENERGY     █████████████████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░ │
│            ▲ onset         ▲ DROP                               │
└─────────────────────────────────────────────────────────────────┘
```

### Settings panel (right side, collapsible)

```
Mode:     [Open Dance ▼]
Palette:  [Open Dance ▼]
Intensity: ────●──────── 80%
Seed:     [42         ] [Re-run]
```

### Keyboard additions for Song Preview

| Key | Action |
|-----|--------|
| `Space` | Play / Pause |
| `←` / `→` | Step backward / forward 5s |
| `[` / `]` | Scrub to previous / next detected phrase |
| `R` | Re-run preview with current settings |
| `S` | Save as Lighting Program |
| `L` | Load Lighting Program |

---

## Implementation Status

### Analysis pipeline ✅

`audio/file_loader.py`, `audio/offline_analyzer.py` — batch FFT, `AnalysisTimeline`.

### Deterministic engine ✅

Clock injection in `EnvelopeFollower`, `PaletteBlender`, `SceneLayout`.
`engine/deterministic.py` drives the engine from a timeline with a fixed seed.

### Visualizer playback ✅

`app/render/playback.py`, `app/render/waveform.py`, `scripts/test_song_preview.py`.

### Saved Program Mode — TODO

1. `data/lighting_program.py` — `LightingProgram` model
2. `data/program_store.py` — JSON save/load
3. Visualizer: save / load / library UI
4. Hybrid playback (program + live reaction)

---

## Dependency Evaluation

### For file loading

| Library | WAV | MP3 | FLAC | Weight | Notes |
|---------|-----|-----|------|--------|-------|
| `scipy.io.wavfile` | ✅ | ❌ | ❌ | ~lightweight | Already available via scipy |
| `soundfile` | ✅ | ❌ | ✅ | Moderate | libsndfile C dependency |
| `librosa` | ✅ | ✅ | ✅ | ~300MB | Best analysis, heavy |
| `pydub` | ✅ | ✅ | ✅ | Moderate | Requires ffmpeg |

**Recommendation:** Use `soundfile` for loading (WAV + FLAC cover most DJ use), add `pydub` as optional for MP3 support with a clear "optional, install ffmpeg" note. Add `librosa` only if onset detection quality requires it.

### For onset/beat detection

| Approach | Quality | Dependency |
|----------|---------|-----------|
| Energy flux threshold | Simple, good for EDM | None (pure numpy) |
| `librosa.onset.onset_detect` | High quality | librosa |
| `madmom` | Excellent, model-based | Heavy |

**Recommendation:** Implement energy flux in `offline_analyzer.py` first (no new dependency). Plug `librosa` in behind a feature flag when better detection is needed.

---

## What NOT to Build

The following are out of scope for the preview mode:

- Acoustic fingerprinting (save using SHA-256 hash, not AcoustID)
- DJ software integration (Serato/Traktor metadata sync)
- Key detection or harmonic analysis
- Crowd reaction / ambient sensing
- ML-based lighting suggestions
- Timeline editor (drag-and-drop phrase markers)
- Multi-track / stem separation
- Haze simulation in visualizer
- 3D visualizer or photorealistic rendering
