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
