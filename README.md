# LightBrain

A wedding/event DJ lighting engine that listens to live audio and drives
professional DMX fixtures in real time. Not a generic sound-active script —
it separates music into **lighting stems** (low, mid, high, energy) and
interprets them through mode profiles, palettes, and scene presets tuned for
wedding moments.

**Current status:** Hardware arrives in ~2 weeks. System runs fully in mock
mode today. 590 automated tests passing (2 skipped on Windows).

---

## How it works

```
DJ audio source (or --demo for synthetic audio)
        │
        ▼
AudioCapture  ──  AudioAnalyzer (FFT: low / mid / high / overall)
                        │
                  BeatDetector  →  BPM estimate
                        │
                  LaneSmoother  (EMA envelope followers)
                        │
                  RoomLane  (mode profile + palette → HSV)
                        │
                  StrobeEngine  (EDM lift strobe, 2–16 Hz)
                        │
                  SafetyEngine  (blackout gate, dimmer, strobe_allowed)
                        │
        ┌───────────────┼─────────────────────┐
        ▼               ▼                     ▼
   Fixtures        SceneLayout           HybridEngine
 (DMX mappers)   (RigVisualState)     (blend saved + live)
        │               │
        ▼               ▼
  DMXUniverse      Visualizer (pygame)
        │
  MockDMXOutput / EnttecProOutput / ArtNetOutput
        │
  Web dashboard + iPad PWA  ◄──  commands (mode / scene / faders)
```

---

## Project structure

```
lightbrain/
  app/
    main.py                 Entry point — all CLI flags live here
    hybrid.py               HybridEngine: blend saved program + live audio
    render/
      fixture_state.py      RigVisualState + FixtureStateTimeline dataclasses
      scene.py              Maps engine output → RigVisualState (all fixtures)
      visualizer.py         Pygame 2D rig preview
      playback.py           FixtureStateTimeline player (song preview)
      waveform.py           Waveform + energy lane display (song preview)
    web/
      server.py             FastAPI web server (dashboard + REST + WebSocket)
      ipad_server.py        iPad PWA server (port 8080)
      dashboard.html        Desktop web dashboard
      ipad.html             iPad PWA controller
  audio/
    input.py                Non-blocking sounddevice capture
    analyzer.py             Real-time FFT band analyzer
    beat_detector.py        BPM estimation from onset intervals
    synthetic.py            Synthetic audio source (no hardware needed)
    offline_analyzer.py     Batch FFT + phrase/onset detection
    file_loader.py          WAV / FLAC / MP3 loader
  engine/
    smoothing.py            EMA EnvelopeFollower + LaneSmoother
    palettes.py             HSV palettes, hue interpolation, hold/transition SM
    lanes.py                RoomLane, impact/room rendering
    modes.py                6 mode definitions (brightness profile, palette key)
    safety.py               Blackout, strobe gate, master dimmer
    gamma.py                Gamma correction (v^2.2, DMX scaling)
    strobe.py               EDM lift strobe engine
    hue_crossfader.py       Smooth hue crossfade on mode switch
    transitions.py          Mode transition handler
    scenes.py               SceneManager + preset activation
    deterministic.py        Seeded replay engine (song preview)
    settings_snapshot.py    Captures engine settings for deterministic replay
  fixtures/
    fixture.py              FixtureBase dataclass
    rockwedge.py            RockWedge 8-ch RGBWAUV (current hardware placeholder)
    chauvet_wash_fx2.py     Chauvet Wash FX2 — 3/4/8/28-ch mapper
    chauvet_gigbar_move_ils.py  Chauvet GigBAR Move+ILS — 29-ch mapper
    djflx_beam.py           DJFLX 10-ch moving beam mapper
    aiming.py               FixtureAimingTool (pan/tilt presets)
    positions.json          Named pan/tilt presets
    states.json             Named color/intensity presets
  dmx/
    universe.py             512-ch DMXUniverse (1-indexed)
    output_mock.py          MockDMXOutput (no hardware needed)
    output_enttec_pro.py    Enttec USB Pro protocol
    output_artnet.py        Art-Net 4 UDP output
  data/
    lighting_program.py     LightingProgram model + fingerprinting
    program_store.py        Save / load / index programs
    setlist.py              Setlist + SetlistEntry models
    setlist_store.py        Setlist file I/O
  midi/
    input.py                MIDI controller input (mido/rtmidi)
  ui/
    terminal_debug.py       ANSI 40 FPS terminal overlay
  config/
    rig_config.json         Fixture layout + hardware settings
    app_config.json         Web server / headless mode toggle
    palettes/               6 palette files (one per mode)
    scenes/                 9 wedding scene presets
  scripts/
    list_audio_devices.py   Find your audio device index
    test_audio_analyzer.py
    test_mock_rockwedge.py  Full pipeline, keyboard mode switching
    test_visualizer.py      2D pygame rig preview
    test_dmxking_rockwedge.py  Hardware DMX test
    test_song_preview.py    Offline analysis + deterministic replay
  tests/
    test_lightbrain.py      590 automated tests (2 skipped on Windows)
  docs/
    ARCHITECTURE.md         System design + module guide
    ROADMAP.md              Sprint history + completed features
```

---

## Installation

Python 3.10+ required.

```bash
git clone <repo>
cd lightbrain

python -m venv venv
source venv/bin/activate        # macOS / Linux
# venv\Scripts\activate         # Windows

pip install -r requirements.txt
```

### System dependency: PortAudio

`sounddevice` needs the PortAudio C library:

```bash
# macOS
brew install portaudio

# Ubuntu / Debian
sudo apt-get install libportaudio2 portaudio19-dev

# Windows — bundled with sounddevice wheel, no extra install
```

---

## Quick start — no hardware needed

```bash
# Synthetic audio, mock DMX, terminal overlay
python -m app.main --demo

# With 2D pygame visualizer
python scripts/test_visualizer.py --simulate

# With web dashboard  →  open http://localhost:8765/
python -m app.main --demo --web

# With iPad controller  →  open http://<your-ip>:8080/ on iPad
python -m app.main --demo --headless --web --ipad-port 8080

# Real audio (find your device index first)
python scripts/list_audio_devices.py
python -m app.main --device 2
```

### CLI flags

| Flag | Description |
|------|-------------|
| `--demo` | Synthetic audio source (no microphone needed) |
| `--device N` | Use audio input device N (from list_audio_devices) |
| `--mode MODE` | Starting mode (open_dance / dinner / banger / indian_latin / speech / slow_dance) |
| `--web` | Enable web dashboard on port 8765 |
| `--web-port N` | Override dashboard port |
| `--web-host HOST` | Bind dashboard to HOST instead of 127.0.0.1 (use 0.0.0.0 for LAN access) |
| `--ipad-port N` | Enable iPad PWA on port N (default 8080) |
| `--headless` | No terminal overlay or pygame window (use with web/iPad) |
| `--serial PORT` | DMX output via Enttec USB Pro (e.g. /dev/ttyUSB0 or COM3) |
| `--artnet IP` | DMX output via Art-Net UDP to IP address |

---

## Web dashboard

Open **http://localhost:8765/** in a browser (or replace `localhost` with your
machine's IP from another device on the same network).

### What you can do

- **Mode** buttons — switch lighting behavior instantly; double-tap to **ARM** a mode (fires automatically on next energy peak or beat)
- **Cooldown indicator** — mode button opacity dims proportionally during the 10 s beat-swap lockout after an energy-triggered palette change
- **Scene presets** — activate wedding moments (Grand Entrance, First Dance, etc.)
- **Strobe speed presets** — SLOW / MED / FAST / MAX preset buttons; ARM button arms strobe for next beat
- **STROBE HOLD** — hold to strobe at current speed; release to stop
- **BUMP** — single full-white 75 ms accent hit
- **Kill switches** — STROBE / DERBY / LASER buttons; when red = that element is silenced
- **BLACKOUT** — instant black on press; 1.5 s linear fade-up on release
- **Canvas** — live 2D fixture visualizer; border glows when strobe fires, flashes on BUMP
- **3D tab** — Three.js rig visualizer in an iframe; left panel mirrors all Live controls; Uplights preview count selector (6 / 12 / 18); WHITE momentary hold button
- **Fader shortcuts** — 0% / 50% / 100% quick-set buttons above the master dimmer

The dashboard connects via WebSocket and updates at ~15 fps. Commands send
instantly via fetch POST.

---

## iPad PWA controller

Open **http://\<your-ip\>:8080/** in Safari on the iPad. Add to Home Screen
for full-screen standalone mode (no browser chrome).

The iPad controller has three tabs:

### PERF tab (live performance)
- **Audio Energy** bars — low / mid / high / overall
- **Mode** buttons — 3 per row; double-tap within 300 ms to **ARM** (fires on next energy peak/beat); armed button pulses blue
- **Cooldown opacity** — mode button alpha scales with the 10 s beat-swap lockout fraction
- **Scene Presets** — F1–F9 shortcuts, Release button
- **Faders** — Master / Uplight / Strobe (synced from engine on connect); 0% / 50% / 100% quick-set buttons above each fader
- **Kill Switches** — STROBE / DERBY / LASER; tap to kill (shows "OFF" + red when killed), tap again to restore
- **Momentary row** — BUMP (tap), STROBE HOLD (hold), WHITE HOLD (hold for full white scene)

### TEST tab (fixture testing, no hardware required)
- Tap any color button to lock all fixtures to that color: Blackout, Dim, White,
  Red, Green, Blue, Yellow, Cyan, Magenta, UV, Amber, Strobe
- Moving head aim snaps: Left / Center / Right pan, Ceiling / Floor / Front tilt
- **RELEASE TEST** — returns to live engine

### SETUP tab
- DMX output type and engine FPS readout
- Fixture aiming joystick — touch-drag to move the spot head
- Save named position presets

---

## Hardware: current rig (DMX adapter arriving ~2 weeks)

### Target fixture list

| ID | Fixture | Type | DMX Addr | Lane | Personality |
|----|---------|------|----------|------|-------------|
| `washfx2_l` | Chauvet Wash FX2 Left  | wash  | 1  | room | 8Ch |
| `washfx2_r` | Chauvet Wash FX2 Right | wash  | 9  | room | 8Ch |
| `gigbar_001` | Chauvet GigBAR Move+ILS | combo | 17 | room | 29Ch |

Set in **config/rig_config.json** — swap the active `fixtures` array from the
placeholder RockWedge to `_hardware_fixtures` when hardware is connected.

### DMX adapter

**Enttec USB Pro** or compatible device.

```bash
# List serial ports
python scripts/test_dmxking_rockwedge.py --list-ports

# Hardware test (fixture must be powered and addressed)
python scripts/test_dmxking_rockwedge.py --port /dev/ttyUSB0

# Run engine with hardware output
python -m app.main --device 2 --serial /dev/ttyUSB0 --web
```

### Art-Net output

```bash
python -m app.main --demo --artnet 2.0.0.1
```

---

## Fixture configuration

Edit **config/rig_config.json**:

```json
{
  "dmx": {
    "output": "enttec",
    "serial_port": "/dev/ttyUSB0"
  },
  "fixtures": [
    {
      "id": "washfx2_l",
      "type": "wash_fx2",
      "dmx_address": 1,
      "lane": "room",
      "group": "left"
    }
  ]
}
```

Supported fixture types: `rockwedge`, `wash_fx2`, `gigbar_move_ils`, `djflx_beam`.

For **GigBAR Move+ILS**, set it to **29Ch** personality on the fixture menu. Optional config keys:

```json
{
  "type": "gigbar_move_ils",
  "dmx_address": 17,
  "spot_pan_deg": 270,
  "spot_tilt_dmx": 90,
  "laser_enabled": false
}
```

---

## Modes

| Key | Name | Character | Palette |
|-----|------|-----------|---------|
| `open_dance` | Open Dance | Moderate pulse, light energy | Blue / Purple / Cyan |
| `dinner` | Dinner | Warm, low brightness, calm | Amber / Champagne / Warm White |
| `banger` | Banger | Full brightness, heavy sparkle, fast | Red / Magenta / Electric Blue |
| `indian_latin` | Indian / Latin | Vibrant, active, lively | Magenta / Gold / Emerald |
| `speech` | Speech | Very calm, professional | Warm White / Amber |
| `slow_dance` | Slow Dance | Soft, romantic, slow beams | Lavender / Blush / Champagne |

Modes gate automatic strobe: only `banger` and `indian_latin` allow the EDM
lift strobe engine to fire. Manual STROBE HOLD works in any mode.

---

## Scene presets

Nine wedding scenes ship in **config/scenes/**. Activate via the dashboard,
iPad, or F1–F9 keyboard shortcut:

| Key | Scene |
|-----|-------|
| F1 | test |
| F2 | Bouquet / Garter |
| F3 | Cake Cutting |
| F4 | Dinner Service |
| F5 | First Dance |
| F6 | Grand Entrance |
| F7 | Last Dance |
| F8 | Open Dancing |
| F9 | Send-Off |
| F10 | Release Scene (return to mode) |

Create custom scenes in the **Scene Editor** tab of the web dashboard.

---

## Audio setup

```bash
# Find your device index
python scripts/list_audio_devices.py
```

**Virtual loopback** (route DJ software output into LightBrain):
- macOS: [BlackHole](https://github.com/ExistentialAudio/BlackHole)
- Windows: [VB-Audio Virtual Cable](https://vb-audio.com/Cable/)

Set `device_index` in `config/rig_config.json` or use `--device N` on the
command line.

---

## Keyboard controls

### Terminal (main app + test scripts)

| Key | Action |
|-----|--------|
| O | Open Dance mode |
| D | Dinner mode |
| B | Banger mode |
| I | Indian / Latin mode |
| S | Speech mode |
| L | Slow Dance mode |
| Space | Blackout toggle |
| F1–F9 | Activate scene preset |
| F10 | Release scene |
| Q | Quit |

### Pygame visualizer

Same mode keys plus:

| Key | Action |
|-----|--------|
| A | Toggle live / simulated audio |
| P | Pause / unpause animation |
| Q / Esc | Quit |

---

## Safety and kill switches

**Blackout** — instantly silences all output. Activates a 0.8-second
fade-out. Available on dashboard, iPad, and Space bar. Restored by pressing again.

**Kill switches** (dashboard and iPad PERF tab):
- **STROBE** — silences all strobe output (auto EDM and manual hold)
- **DERBY** — stops the GigBAR derby rotation and blanks derby colors
- **LASER** — forces GigBAR laser off regardless of config

Kill switches are independent of mode — they override at the last step
before DMX write. Red label ("STROBE OFF") = element is silenced.

---

## Run the test suite

```bash
python -m pytest tests/ -v
```

580 tests covering: audio analysis, beat detection, gamma, DMX universe,
smoothing, palettes, modes, safety, room lane, all fixture mappers, scene
system, strobe engine, hue crossfade, Art-Net, hybrid blend, program
save/load, setlist, web API, iPad commands, and the full codebase audit.

---

## Troubleshooting

**`OSError: PortAudio library not found`**
Install the system library (see Installation above). `pip` alone is not enough.

**`PortAudioError: Error querying device -1`**
No audio hardware detected. Use `--demo` or `--simulate`.

**BPM reads too high (200+)**
The beat detector caps at 180 BPM and normalizes by halving. If you still
see high readings, the audio signal has a lot of transients — this is normal
in demo mode.

**Visualizer window won't open**
Requires pygame 2.5+: `pip install "pygame>=2.5.0"`. On headless servers
set `SDL_VIDEODRIVER=dummy` (tests only).

**DMX channels all zero on startup**
Room energy build time varies by mode: ~0.4s for banger, ~1.5s for dinner/speech — each mode has a dedicated EMA profile. Wait a moment after startup.
Wait a moment — values will appear.

**Flash / Strobe look like nothing happens in the visualizer**
The canvas visualizer draws lighting intent state, not per-frame DMX bytes.
On real hardware, strobe fires at the DMX level. Watch the canvas border:
it glows yellow while strobe hold is active, pops blue-white on a flash.

**`ModuleNotFoundError`**
Always run from the repo root:
```bash
cd /path/to/lightbrain
python -m app.main --demo
```

---

## Architecture and design

See **docs/ARCHITECTURE.md** for the full system design, module breakdown,
safety priority chain, and determinism contract.

See **docs/ROADMAP.md** for the complete sprint history.
