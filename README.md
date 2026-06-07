# RKADE LightBrain — Sprint 1B + Visualizer MVP

A mobile-DJ-focused lighting engine that listens to live audio, separates it
into lighting-control stems, smooths those stems through EMA envelope followers,
applies musical interpretation rules, and renders DMX values for fixtures.

**This is not a generic sound-active script.** It is a premium "lighting stems"
engine where lows, mids, highs, energy, palettes, mode profiles, transitions,
and safety rules control fixture behavior.

---

## MVP Pipeline

```
DJ audio source  (or SyntheticAudioSource --simulate)
      |
      v
AudioCapture  (sounddevice ring-buffer)
      |
      v
AudioAnalyzer  (FFT bands: low / mid / high / overall, adaptive gain)
      |
      v
LaneSmoother  (EMA followers: Impact 10ms/250ms, Room 600ms/3000ms)
      |
      v
RoomLane  (mode brightness profile + palette hold/transition + bass breathing -> HSV)
      |
      v
SafetyEngine  (blackout gate, mode intensity scale, master dimmer)
      |
      v
┌──────────────────┐    ┌──────────────────────────┐
│  RockWedge       │    │  SceneLayout (visualizer) │
│  HSV→RGB→gamma   │    │  builds RigVisualState    │
│  → 8-ch DMX      │    │  for all 18+5 fixtures    │
└──────────────────┘    └──────────────────────────┘
      |                        |
      v                        v
DMXUniverse              Visualizer (pygame 2D)
(512-ch buffer)          animated rig preview
      |
      v
MockDMXOutput / EnttecProOutput → DMXking ultraDMX MAX
```

---

## Project Structure

```
lightbrain/
  app/
    main.py               Full app entry point (--device, --mode, --demo/--simulate)
    render/
      fixture_state.py    High-level fixture state dataclasses (UplightState etc.)
      scene.py            SceneLayout: 18 uplights + 2 washes + 2 beams + sparkle + impact
      visualizer.py       Pygame renderer: glows, washes, beam cones, sparkles, HUD
  audio/
    input.py              Non-blocking AudioCapture via sounddevice
    analyzer.py           FFT band energy analyzer (low/mid/high/overall)
    synthetic.py          Synthetic audio source for demo/testing (no mic needed)
  engine/
    smoothing.py          EMA EnvelopeFollower + LaneSmoother (Impact, Room)
    palettes.py           HSV palettes, shortest-path hue interpolation, hold/transition SM
    gamma.py              Gamma correction (v^2.2, clamp, DMX scaling)
    lanes.py              RoomLane renderer (mode brightness profile + saturation scale)
    modes.py              Mode definitions with brightness profiles (base/max/pulse/sat/hold)
    safety.py             Safety engine (blackout, strobe disable, master dimmer)
  dmx/
    universe.py           512-ch DMXUniverse (1-indexed public API)
    output_mock.py        MockDMXOutput (logs changes, no hardware needed)
    output_enttec_pro.py  EnttecProOutput (hardware-ready, untested)
  fixtures/
    fixture.py            FixtureBase dataclass
    rockwedge.py          RockWedge 8-ch RGBWAUV mapper
  ui/
    terminal_debug.py     ANSI refreshing terminal overlay (40 FPS)
  config/
    rig_config.json       Rig configuration
    palettes/             dinner, open_dance, banger, indian_latin, speech, slow_dance
  tests/
    test_lightbrain.py    115 automated tests (pytest)
  scripts/
    list_audio_devices.py
    test_audio_analyzer.py
    test_mock_rockwedge.py  Full mock pipeline with keyboard mode switching
    test_visualizer.py      2D pygame rig preview
    test_dmxking_rockwedge.py
```

---

## Installation

Python 3.10+ recommended.

```bash
cd lightbrain

# Create virtual environment (recommended)
python -m venv venv
source venv/bin/activate        # macOS/Linux
# venv\Scripts\activate         # Windows

# Install Python dependencies
pip install -r requirements.txt
```

### System dependency: PortAudio

`sounddevice` requires the PortAudio C library. Install it for your OS:

**macOS:**
```bash
brew install portaudio
```

**Ubuntu/Debian:**
```bash
sudo apt-get install libportaudio2 portaudio19-dev
```

**Windows:**
PortAudio is bundled with the sounddevice wheel. No manual install needed.

### pygame

pygame is installed automatically via `pip install -r requirements.txt`.
No additional system libraries are required for most platforms.

---

## Quick start — no hardware needed

```bash
# 1. List available audio devices
python scripts/list_audio_devices.py

# 2. Run audio analyzer test (demo mode — no microphone needed)
python scripts/test_audio_analyzer.py --demo

# 3. Run full mock pipeline with keyboard mode switching (demo mode)
python scripts/test_mock_rockwedge.py --demo

# 4. Run the 2D rig visualizer (simulated audio)
python scripts/test_visualizer.py --simulate

# 5. Run with real microphone (replace 2 with your device index)
python scripts/test_mock_rockwedge.py --device 2

# 6. Run full app
python -m app.main --demo
python -m app.main --device 2 --mode dinner
```

Press `Ctrl+C` to stop terminal scripts. Press `Q` or `Esc` in the visualizer.

---

## Preview Visualizer

The 2D rig preview lets you see how modes and palettes look before
connecting physical fixtures.

```bash
# Simulated audio (no microphone or routing needed):
python scripts/test_visualizer.py --simulate

# Try live audio, fall back to simulated if unavailable:
python scripts/test_visualizer.py

# Choose a starting mode:
python scripts/test_visualizer.py --simulate --mode banger
```

### What you'll see

- **18 uplights** glowing and pulsing around the dance floor perimeter
- **2 wash effects** (Chauvet Wash FX) flanking the DJ booth
- **1 GigBAR central wash** with sparkle/derby dots on the dance floor
- **2 DJFLX beams** sweeping slowly from the DJ area toward the floor
- **Impact flash** overlay in energetic modes (Banger, Open Dance, Indian/Latin)
- **Blackout** immediately blacks out everything with a large BLACKOUT label

### Visualizer keyboard controls

| Key    | Action           |
|--------|------------------|
| O      | Open Dance       |
| D      | Dinner           |
| B      | Banger           |
| I      | Indian / Latin   |
| S      | Speech           |
| L      | Slow Dance       |
| Space  | Blackout toggle  |
| A      | Toggle Live/Simulated audio |
| P      | Pause / unpause animation |
| Q / Esc | Quit           |

### Mode visual differences

| Mode        | Look                                                 |
|-------------|------------------------------------------------------|
| Dinner      | Warm amber/champagne, low brightness, no sparkle     |
| Speech      | Warm white/amber, very calm, no sparkle or flash     |
| Open Dance  | Blue/purple, moderate pulse, light sparkle           |
| Banger      | Full brightness, heavy sparkle, impact flashes, fast beams |
| Indian/Latin| Saturated magenta/gold, lively sparkle, active beams |
| Slow Dance  | Soft lavender/blush, slow beams, no sparkle          |

### Known visualizer limitations

- The scene is a stylized 2D layout, not a room photo or 3D model
- No haze, gobos, or optical simulation
- All 18 uplights show the same color (individual addressing is Sprint 2+)
- Beam spread is simplified (no gobo patterns)
- Sparkle is randomized, not physics-based
- GigBAR beam lines not yet shown (central wash + sparkle only)

---

## Keyboard mode switching (terminal scripts)

Both `scripts/test_mock_rockwedge.py` and `python -m app.main` support
live keyboard mode switching in a real terminal:

| Key    | Mode           |
|--------|----------------|
| O      | Open Dance     |
| D      | Dinner         |
| B      | Banger         |
| I      | Indian / Latin |
| S      | Speech         |
| L      | Slow Dance     |
| Space  | Blackout toggle|
| Q      | Quit           |

---

## Audio devices

```bash
python scripts/list_audio_devices.py
```

Find your DJ monitor or virtual loopback device index.

**Virtual loopback for DJ software:**
- macOS: BlackHole (https://github.com/ExistentialAudio/BlackHole)
- Windows: VB-Audio Virtual Cable (https://vb-audio.com/Cable/)

After installing, route your DJ software output to the virtual device,
then select that device index with `--device N`.

---

## Modes and palettes

| Key            | Display        | Colors                                          |
|----------------|----------------|-------------------------------------------------|
| `open_dance`   | Open Dance     | Royal Blue / Purple / Cyan / Magenta            |
| `dinner`       | Dinner         | Warm Amber / Champagne / Warm White / Soft Rose |
| `banger`       | Banger         | Red / Magenta / Electric Blue / White           |
| `indian_latin` | Indian / Latin | Magenta / Gold / Emerald / Royal Blue / Amber   |
| `speech`       | Speech         | Warm White / Amber / Champagne                  |
| `slow_dance`   | Slow Dance     | Soft Lavender / Blush / Champagne / Warm White  |

Each mode now has a **brightness profile** (Sprint 1B):

| Mode        | Base brt | Max brt | Pulse | Sat scale | Hold/color |
|-------------|----------|---------|-------|-----------|------------|
| Open Dance  | 0.20     | 1.00    | 0.15  | 1.00      | 4s         |
| Dinner      | 0.35     | 0.65    | 0.06  | 0.85      | 8s         |
| Banger      | 0.30     | 1.00    | 0.20  | 1.00      | 2s         |
| Indian/Latin| 0.25     | 1.00    | 0.18  | 1.00      | 3s         |
| Speech      | 0.50     | 0.70    | 0.02  | 0.50      | 15s        |
| Slow Dance  | 0.40     | 0.75    | 0.08  | 0.75      | 10s        |

---

## Run the automated tests

```bash
pip install pytest   # one-time
python -m pytest tests/ -v
```

All **115 tests** should pass. Tests cover:
- Hue interpolation (shortest-path, wraparound)
- Gamma correction (curve, clamp, DMX scaling)
- DMX universe (channel API, clamping, blackout, diff)
- Smoothing (bounds, NaN safety, cooldown hold, attack/decay timing)
- Palette loading (all 6 palettes, graceful failure on bad JSON)
- Palette hold/transition state machine (HOLDING/TRANSITIONING states)
- RockWedge mapper (channel writes, dimmer/RGB separation, sprint constraints)
- Safety engine (blackout, mode scale, master dimmer, strobe disabled)
- Mode brightness profiles (base/max/pulse/saturation per mode)
- Full room lane pipeline (end-to-end bounds check, mode profile behavior)
- Synthetic audio (valid blocks, non-zero energy, no NaN/Inf)
- Fixture state dataclasses (instantiation, field types)
- Scene layout (18 uplights, RGB validity, blackout suppression, sparkle per mode)

---

## DMXking ultraDMX MAX (hardware — when it arrives)

**Do not run this without a DMX fixture connected, powered, and addressed correctly.**

```bash
# List available serial ports
python scripts/test_dmxking_rockwedge.py --list-ports

# Run hardware test: blackout -> red -> green -> blue -> white -> fade -> blackout
python scripts/test_dmxking_rockwedge.py --port /dev/ttyUSB0
python scripts/test_dmxking_rockwedge.py --port COM3 --address 1

# Run full app with hardware DMX output
python -m app.main --device 2 --serial /dev/ttyUSB0
```

The `output_enttec_pro.py` module is fully implemented using the Enttec USB Pro
protocol. It has not been tested against physical hardware yet.

---

## RockWedge channel map (provisional — verify against physical unit)

| Ch | Function | Sprint 1 |
|----|----------|----------|
| 1  | Dimmer   | Gamma-corrected brightness |
| 2  | Red      | Pure hue gamma-corrected (no brightness in channel) |
| 3  | Green    | Pure hue gamma-corrected |
| 4  | Blue     | Pure hue gamma-corrected |
| 5  | White    | 0 (Sprint 2: palette-driven) |
| 6  | Amber    | 0 (Sprint 2: palette-driven) |
| 7  | UV       | 0 (Sprint 2: palette-driven) |
| 8  | Strobe   | 0 (globally disabled Sprint 1) |

**Dimmer/RGB design:** Ch1 carries the overall brightness. Ch2-4 carry pure hue
at full value. The fixture hardware multiplies dimmer × colour to produce the
actual output — this avoids double-dimming and gives correct perceptual response.

---

## Troubleshooting

**`OSError: PortAudio library not found`**
Install the system library (see Installation above). This is separate from the
Python package — pip alone is not enough.

**`PortAudioError: Error querying device -1` / no devices listed**
No audio hardware in this environment. Use `--demo` or `--simulate` flag.

**Visualizer window doesn't open / pygame error**
Make sure pygame 2.5+ is installed: `pip install "pygame>=2.5.0"`
On headless servers, set `SDL_VIDEODRIVER=dummy` for unit tests only.

**Overlay is scrolling instead of staying fixed**
ANSI cursor control requires a real TTY. If you pipe output to a file or log,
it will scroll. Run interactively in a terminal.

**FPS shows `---` on first frame**
Normal — the overlay skips the first sample to avoid a false spike. FPS
stabilises to ~40 by the second frame.

**DMX channels all zero**
Room energy takes ~1-2 seconds to build up from silence (600ms attack tau).
Make sure audio is actually playing. With `--demo`, values appear within 1s.

**`ModuleNotFoundError`**
Run scripts from the repo root, not from inside a subdirectory:
```bash
cd /path/to/lightbrain
python scripts/test_mock_rockwedge.py --demo
```

---

## Current assumptions

- One RockWedge-style uplight at DMX address 001, 8-channel RGBWAUV mode
- Gamma = 2.2 (standard sRGB; adjust per fixture if needed)
- Audio block size = 1024 samples at 44100 Hz = ~23ms per FFT frame
- Gain normalization is adaptive (running-peak RMS, per band)
- Strobe is disabled globally in Sprint 1
- White / Amber / UV channels are zero (palette-driven logic Sprint 2)
- Palette transitions use hold/blend state machine (hold_ms per mode, then transition_ms blend)

---

## Not yet built (Sprint 2+)

- Impact Lane (strobe/flash behaviors on real DMX)
- White / Amber / UV channel control
- GigBAR beam lines in visualizer
- Individual uplight addressing (currently all 18 show the same color)
- Floor / Beam / Sparkle lanes wired to DMX
- Wash effects, moving heads, GigBAR full ILS control
- MIDI control input
- Art-Net / sACN network output
- Raspberry Pi deployment
- GUI / web dashboard
- Multi-fixture group management
- Fixture wizard / channel mode configurator
- Beat / phrase detection for palette transitions
