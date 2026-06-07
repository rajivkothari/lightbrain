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

**Status: Planned**

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

**Status: Planned — see `docs/SONG_PREVIEW_MODE.md` for full spec**

This is the next major feature. It allows a DJ to load an audio file and
preview the likely lighting behavior before the gig.

### Core deliverables

- [ ] `audio/file_loader.py` — WAV/MP3/FLAC → numpy array using scipy.io.wavfile or librosa
- [ ] `audio/offline_analyzer.py` — batch FFT across full song + onset detection + phrase/drop/silence markers → `AnalysisTimeline`
- [ ] `engine/deterministic.py` — drives existing smoothers and lanes from `AnalysisTimeline` with fixed clock + seeded RNG → `FixtureStateTimeline`
- [ ] `app/render/playback.py` — plays back `FixtureStateTimeline` with a playhead cursor
- [ ] `app/render/waveform.py` — waveform + low/mid/high/energy lane display in visualizer
- [ ] `scripts/test_song_preview.py` — song preview runner (CLI: `python scripts/test_song_preview.py --file song.wav --mode banger`)
- [ ] Visualizer keyboard: `[` / `]` scrub, `Space` play/pause, `R` re-run preview
- [ ] Settings panel: mode, palette, intensity, seed selection before/during preview

### Determinism requirements

See `docs/SONG_PREVIEW_MODE.md` → Determinism Requirements section.

Short version: same file + same mode + same settings + same seed = identical preview every run.

### Dependencies to evaluate

- `librosa` — full-featured audio analysis (onset, beat tracking, spectral features, BPM). Heavy dependency (~300MB), but best in class.
- `scipy` + `soundfile` — lightweight alternative for WAV analysis. Limited onset detection.
- Decision: spike `librosa` for onset detection quality. Use `soundfile` for file loading only if librosa is too heavy.

---

## Phase 4 — Sprint 4: Saved Program Mode

**Status: Planned — see `docs/SONG_PREVIEW_MODE.md` → Saved Program Mode section**

After previewing a song, the DJ can save the generated lighting pass as a
**Lighting Program**. This program can later be replayed exactly or used as a
guide while still reacting live.

### Core deliverables

- [ ] `data/lighting_program.py` — `LightingProgram` dataclass (full spec in SONG_PREVIEW_MODE.md)
- [ ] `data/program_store.py` — save/load to JSON + optional SQLite
- [ ] Program library UI in visualizer (list, load, delete programs)
- [ ] Playback modes: exact replay / hybrid (program + live reaction) / live override
- [ ] Song fingerprinting for automatic program matching (hash or acoustic fingerprint)
- [ ] Export: share program as JSON file

---

## Phase 5 — Advanced Future

**Status: Research / Backlog**

### Intelligence

- [ ] Song fingerprint library — auto-match loaded song to saved program
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
| 2 | ~140 | Beat detection, phrase detection, MIDI |
| 3 | ~180 | OfflineAnalyzer, DeterministicEngine, determinism contract |
| 4 | ~220 | LightingProgram save/load, replay correctness |
