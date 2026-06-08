# LightBrain Roadmap

All sprints through the codebase audit are complete. This document records
what was built in each sprint and what comes next.

---

## Sprint 1 / 1B — Core Engine MVP ✅

**115 tests passing**

- Live audio capture via sounddevice (non-blocking ring buffer)
- FFT band energy analyzer (low/mid/high/overall, adaptive gain)
- EMA envelope followers with separate attack/decay taus
- Impact + Room lanes
- HSV palettes with shortest-path hue interpolation
- Palette hold/transition state machine (HOLDING → TRANSITIONING)
- Per-mode brightness profiles (base/max brightness, pulse, saturation, hold_ms)
- Gamma correction (v^2.2, clean dimmer/RGB separation)
- 512-channel DMX universe (1-indexed, numpy uint8)
- RockWedge 8-channel RGBWAUV mapper
- MockDMXOutput (terminal overlay)
- EnttecProOutput (Enttec USB Pro framing)
- ANSI terminal overlay (FPS, bands, lanes, color names, hold timer)
- Synthetic audio source (deterministic drum machine)
- 2D Preview Visualizer (pygame): uplights, washes, beam cones, sparkles, impact flash, blackout
- Non-blocking keyboard mode switching (O/D/B/I/S/L/Space/Q)
- Fixture State Model (`RigVisualState`, `FixtureStateTimeline` stub)
- Architecture docs, roadmap, Song Preview Mode planning

---

## Sprint 2 — Near-Term Polish ✅

**143 tests passing**

- White / Amber / UV channel control (palette-driven per mode)
- Beat detection: energy-threshold onset algorithm for palette transition triggers
- BPM estimation from inter-beat intervals
- Mode transition animations (smooth hue crossfade on mode switch)
- Individual uplight addressing (per-fixture color group support)
- MIDI controller input (mido/rtmidi, CC messages → mode/blackout)
- GigBAR / Wash FX / DJFLX DMX channel maps (initial versions)

---

## Sprint 3 — Song Preview Mode ✅

**189 tests passing**

- Audio file loader (WAV/FLAC/MP3 → numpy via soundfile)
- Offline analyzer (batch FFT + onset/phrase/drop detection → AnalysisTimeline)
- DeterministicEngine (seeded, injectable clock, replays AnalysisTimeline)
- FixtureStateTimeline playback controller with playhead
- Waveform + energy lane display in visualizer
- `scripts/test_song_preview.py` runner
- Keyboard: Space (play/pause), ←→ (±5s seek), R (re-run), Ctrl+S/L (save/load)

---

## Sprint 4 — Saved Program Mode ✅

**223 tests passing**

- `LightingProgram` model (identity, source, settings snapshot, analysis, generated output, metadata)
- `ProgramStore` — save/load/index programs as JSON
- Song fingerprinting for automatic matching
- Save generated lighting pass; reload for exact replay

---

## Sprint 5 — Hybrid Playback + Art-Net ✅

**261 tests passing**

- `HybridEngine` — blends saved LightingProgram with live audio reactivity
- Art-Net 4 UDP output (unicast and broadcast, full 512-channel packet)
- Auto song matching via fingerprint lookup

---

## Sprint 6 — Setlist Mode ✅

**298 tests passing**

- `Setlist` + `SetlistEntry` models
- Setlist file I/O (`SetlistStore`)
- DJFLX Beam mapper (10-ch pan/tilt moving beam)
- Auto-detection: match incoming audio fingerprint against setlist

---

## Sprint 7 — Preset System ✅

**344 tests passing**

- Named pan/tilt position presets (`fixtures/positions.json`)
- Named color/intensity state presets (`fixtures/states.json`)
- Scene presets (JSON per scene, per-group fixture overrides)
- `SceneManager` — load/activate/release scenes
- F1–F9 keyboard shortcuts for scene activation

---

## Sprint 8 — Web Dashboard ✅

**359 tests passing**

- FastAPI server (port 8765)
- WebSocket state push at ~10 fps
- REST: GET /api/state, POST /api/command
- `dashboard.html` — mode buttons, scene grid, BLACKOUT
- Shared `_engine_state` dict and `_command_queue` between web and engine

---

## Sprint 9 — Canvas Visualizer ✅

**378 tests passing**

- 2D canvas rig visualizer in the dashboard (uplights, washes, beams, sparkle, impact)
- Scene Editor tab — create/edit/delete scene JSON files via REST API
- `/api/scenes` CRUD endpoints
- `/api/presets` — position and state dropdowns for the editor

---

## Sprint 10 — EDM Lift Strobe ✅

**399 tests passing**

- `StrobeEngine` — 2–16 Hz frequency ramp proportional to high-band energy
- Duty cycle and hold parameters
- Strobe gated by `safety.state.strobe_allowed` (banger + indian_latin only)
- RockWedge Ch8 wired to strobe output
- Visual flash overlay in pygame visualizer

---

## Sprint 11 — Live Visual Quality ✅

**414 tests passing**

- Per-fixture color zones (top/bottom/left/right — individual addressing)
- Hue crossfader on mode switch (smooth transition, not snap)
- Ambient warm tint in canvas (dinner/slow_dance/indian_latin modes)

---

## Sprint 12 — Strobe Master ✅

**414 tests passing**

- Strobe master fader (0–1 multiplier applied to auto EDM strobe)
- Lighting lanes UI clarity (impact/room/strobe bars on dashboard)

---

## Sprint 13 — iPad Web Controller ✅

**422 tests passing** *(580 as of current branch)*

- iPad PWA (port 8080) — full-screen standalone, dark theme, large touch targets
- Master / uplight / strobe fader sync from engine state
- FLASH momentary (single hit)
- STROBE HOLD (hold for continuous; release to stop)
- `ipad_server.py` separate FastAPI app
- `--headless` mode (disables terminal overlay and pygame)

---

## Codebase Audit ✅

**437 tests passing (post-audit)** *(580 as of current branch)*

- 9 bugs fixed (including blackout fade logic, EnvelopeFollower initialization)
- 10 logic errors corrected
- 5 technical debt items resolved

---

## Post-Audit Additions (current branch)

These were added after the formal audit in direct development:

- **Blackout fade** — 0.8s alpha-blend fade-out instead of hard cut; snapshots render values at toggle time
- **Fixture test page** — iPad TEST tab with 12 color patterns, moving head aim snaps, RELEASE TEST
- **Chauvet Wash FX2 mapper** (`fixtures/chauvet_wash_fx2.py`) — 8Ch mode, full RGBUV+strobe
- **Chauvet GigBAR Move+ILS mapper** (`fixtures/chauvet_gigbar_move_ils.py`) — 29Ch: par, derby, flash LEDs, laser, spot head
- **Kill switches** — STROBE / DERBY / LASER toggle buttons on dashboard and iPad; override at last step before DMX write
- **Manual strobe bypass** — STROBE HOLD and STROBE BURST no longer gated by `strobe_allowed`; respect strobe_master level
- **Strobe master on manual** — hold/burst use `_strobe_master` instead of hardcoded 1.0
- **STROBE HOLD + FLASH on dashboard** — momentary buttons added to desktop UI
- **BPM fix** — minimum beat interval raised from 200ms to 333ms (max 180 BPM); halving normalization added
- **Mode button cleanup** — removed redundant key sub-label from buttons (iPad + dashboard)
- **Flash/strobe canvas feedback** — canvas border glows yellow on strobe, pops blue-white on flash
- **Derby kill on GigBAR** — `set_derby_enabled()` blanks all derby DMX channels when killed
- **Strobe hold software oscillator** — hold button now flickers at 2–16 Hz (controlled by strobe master fader); previously only drove hardware strobe channel
- **Kill switch per-frame enforcement** — derby/laser kills re-applied every render frame; previously fire-and-forget on toggle
- **FLASH renamed to BUMP** — clarifies it is a 75ms full-white accent hit, not a continuous strobe
- **DMX address collision checker** — `check_dmx_address_map()` validates layout at startup; raises with full address table on overlap or out-of-bounds; `channel_count` property added to all fixture types

---

## Hardware readiness checklist (DMX adapter arriving ~2 weeks)

### 0. Linux / Raspberry Pi setup (one-time)

- [ ] Plug in DMXking; run `lsusb` — confirm FTDI shows `0403:6001`
- [ ] Confirm VCP device enumerated: `ls /dev/ttyUSB*` (should see `/dev/ttyUSB0`)
- [ ] If `/dev/ttyUSB*` missing: `sudo modprobe ftdi_sio` (stock RPi OS loads it automatically)
- [ ] Add user to `dialout` group: `sudo usermod -aG dialout $USER` then **log out / back in**
- [ ] Install latency rule: `sudo cp deploy/99-dmxking-latency.rules /etc/udev/rules.d/`
- [ ] Reload udev: `sudo udevadm control --reload-rules && sudo udevadm trigger`
- [ ] Verify latency: `cat /sys/class/tty/ttyUSB0/device/latency_timer` → should read `1`

### 1. DMX configuration — address programming workflow

DMX addresses must be consistent across three places:

```
Physical fixture menu  →  rig_config.json dmx_address  →  LightBrain startup check
```

Wireless DMX is a transparent relay — no addressing changes needed on the radio link.

**Step-by-step:**

- [ ] On each physical fixture, set start address and personality:
  - Wash FX2 Left → address **1**, personality **8Ch**
  - Wash FX2 Right → address **9**, personality **8Ch**
  - GigBAR Move+ILS → address **17**, personality **29Ch**
- [ ] In `config/rig_config.json`:
  - Replace `fixtures` array with the `_hardware_fixtures` block
  - Set `dmx.output` to `"enttec_pro"` and `dmx.serial_port` to the USB port
  - Confirm `dmx_address` values match the addresses set above
- [ ] Run `python -m app.main` — the startup collision check validates the layout
  before the first DMX frame. Any mismatch prints a clear error and exits cleanly.

**Address map reference:**

| Fixture | Start | Channels | End |
|---------|------:|:--------:|----:|
| Wash FX2 Left | 1 | 8 | 8 |
| Wash FX2 Right | 9 | 8 | 16 |
| GigBAR Move+ILS | 17 | 29 | 45 |

### 2. Smoke tests

- [ ] Run `python scripts/test_dmxking_rockwedge.py --port <PORT>` against each fixture
- [ ] Test full pipeline: `python -m app.main --device <N> --serial <PORT> --web`
- [ ] Tune `spot_pan_deg` and `spot_tilt_dmx` in rig_config for venue aim
- [ ] Test blackout fade, kill switches, and fixture test page against hardware

---

## Future / nice-to-have

- Raspberry Pi deployment (headless, autostart on boot)
- sACN / E1.31 output
- Fixture wizard (interactive DMX address/mode configurator)
- Song fingerprint library auto-matching (DJ software integration)
- Per-song saved programs for wedding setlist replay
- Multi-venue rig profiles
- Tablet scene editor (touch-optimized scene builder)
