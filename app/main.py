"""
LightBrain Sprint 2 — main application entry point.

Pipeline:
  audio input → FFT analysis → beat detection → EMA smoothing →
  room lane (with WAU channels) → RockWedge mapper → DMX universe →
  mock/hardware output → terminal overlay

New in Sprint 2:
  - White/Amber/UV channel control (palette-driven per mode)
  - Beat detection with BPM estimation
  - Smooth mode crossfade via ModeTransitioner
  - MIDI CC input (requires mido[ports-rtmidi])
  - Multi-fixture support (all fixtures from rig_config.json)

Run from the repo root:
  python -m app.main --demo               # synthetic audio, no mic
  python -m app.main --simulate           # same as --demo
  python -m app.main --device N           # real mic, device index N
  python -m app.main --device N --mode dinner

Keyboard controls (in a real terminal):
  O = Open Dance   D = Dinner   B = Banger
  I = Indian/Latin S = Speech   L = Slow Dance
  Space = Blackout toggle       Q = Quit
"""

import argparse
import colorsys
import json
import os
import queue as _queue
import sys
import threading
import time

# ---- path setup (run from repo root) ----
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from audio.analyzer    import AudioAnalyzer, AudioBands
from audio.synthetic   import SyntheticAudioSource
from audio.beat_detector import BeatDetector

from engine.smoothing  import LaneSmoother
from engine.palettes   import load_all_palettes, PaletteBlender
from engine.lanes      import RoomLane
from engine.modes      import get_mode, MODES, KEYBOARD_MAP
from engine.safety     import SafetyEngine
from engine.transitions import ModeTransitioner

from dmx.universe          import DMXUniverse
from dmx.output_mock       import MockDMXOutput
from dmx.output_enttec_pro import EnttecProOutput

from fixtures.rockwedge import RockWedge

from ui.terminal_debug import TerminalDebugOverlay
from engine.scenes import SceneManager

try:
    from midi.input import MidiInput
except ImportError:
    MidiInput = None


# ---- constants ----
CONFIG_PATH    = os.path.join(ROOT, "config", "rig_config.json")
PALETTES_DIR   = os.path.join(ROOT, "config", "palettes")
SCENES_DIR     = os.path.join(ROOT, "config", "scenes")
POSITIONS_FILE = os.path.join(ROOT, "fixtures", "positions.json")
STATES_FILE    = os.path.join(ROOT, "fixtures", "states.json")
TARGET_FPS     = 40
FRAME_TIME     = 1.0 / TARGET_FPS

# F-key escape sequences → key names (xterm / VT100 / Linux console)
_ESCAPE_MAP = {
    "\x1bOP":    "F1",  "\x1b[11~": "F1",
    "\x1bOQ":    "F2",  "\x1b[12~": "F2",
    "\x1bOR":    "F3",  "\x1b[13~": "F3",
    "\x1bOS":    "F4",  "\x1b[14~": "F4",
    "\x1b[15~":  "F5",
    "\x1b[17~":  "F6",
    "\x1b[18~":  "F7",
    "\x1b[19~":  "F8",
    "\x1b[20~":  "F9",
    "\x1b[21~":  "F10",
}
_SCENE_FKEYS = {"F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9"}


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Non-blocking keyboard input (Unix only)
# ---------------------------------------------------------------------------

_key_queue: _queue.Queue = _queue.Queue()
_kbd_stop  = threading.Event()


def _keyboard_thread_fn() -> None:
    if not sys.stdin.isatty():
        return
    try:
        import tty
        import termios
        import select
        fd           = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setcbreak(fd)
            while not _kbd_stop.is_set():
                r, _, _ = select.select([fd], [], [], 0.05)
                if r:
                    ch = os.read(fd, 1).decode("utf-8", errors="replace")
                    if ch == "\x1b":
                        # Try to read the rest of the escape sequence
                        r2, _, _ = select.select([fd], [], [], 0.05)
                        if r2:
                            rest = os.read(fd, 5).decode("utf-8", errors="replace")
                            key  = _ESCAPE_MAP.get("\x1b" + rest, "esc")
                        else:
                            key = "esc"
                        _key_queue.put_nowait(key)
                    else:
                        _key_queue.put_nowait(ch.lower())
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
    except Exception:
        pass


def _start_keyboard() -> threading.Thread:
    t = threading.Thread(target=_keyboard_thread_fn, daemon=True)
    t.start()
    return t


def _stop_keyboard(thread: threading.Thread) -> None:
    _kbd_stop.set()
    thread.join(timeout=0.3)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="LightBrain Sprint 2")
    parser.add_argument("--device",      type=int,  default=None,
                        help="sounddevice input device index")
    parser.add_argument("--demo",        "--simulate", dest="demo",
                        action="store_true",
                        help="use synthetic audio — no microphone needed")
    parser.add_argument("--palette",     type=str,  default=None,
                        help="starting palette key (e.g. open_dance)")
    parser.add_argument("--mode",        type=str,  default=None,
                        help="starting mode key (e.g. dinner)")
    parser.add_argument("--serial",      type=str,  default=None,
                        help="serial port for DMXking (e.g. /dev/ttyUSB0)")
    parser.add_argument("--verbose-dmx", action="store_true",
                        help="print every changed DMX channel to console")
    parser.add_argument("--midi",        type=str,  default=None,
                        help="MIDI input port name (default: first available)")
    args = parser.parse_args()

    config = load_config()

    # ---- palettes ----
    all_palettes = load_all_palettes(PALETTES_DIR)
    if not all_palettes:
        print("[ERROR] No palettes found in config/palettes/")
        sys.exit(1)

    # ---- mode / palette init ----
    mode_key    = args.mode    or config.get("default_mode",    "open_dance")
    palette_key = args.palette or config.get("default_palette", "open_dance")

    current_mode    = get_mode(mode_key)
    current_palette = all_palettes.get(palette_key, list(all_palettes.values())[0])

    # ---- audio setup ----
    error_msg = None

    if args.demo:
        capture      = SyntheticAudioSource(block_size=1024)
        capture.start()
        device_label = capture.device_name
    else:
        from audio.input import AudioCapture
        device_index = (args.device if args.device is not None
                        else config["audio"].get("device_index"))
        sample_rate  = config["audio"]["sample_rate"]
        block_size   = config["audio"]["block_size"]
        capture      = AudioCapture(device_index, sample_rate, block_size)
        try:
            capture.start()
            device_label = capture.device_name or "unknown device"
        except Exception as e:
            error_msg    = f"Audio start failed: {e}"
            device_label = "no device"

    analyzer     = AudioAnalyzer()
    beat_detector = BeatDetector()

    # ---- engine ----
    smoother    = LaneSmoother()
    safety      = SafetyEngine()
    safety.update_from_mode(current_mode)
    room_lane   = RoomLane(current_palette, mode=current_mode)
    transitioner = ModeTransitioner(current_mode)

    # ---- fixtures ----
    universe  = DMXUniverse()
    fixtures  = []
    for fx_cfg in config.get("fixtures", []):
        if fx_cfg.get("type") == "rockwedge":
            fixtures.append(RockWedge(
                fixture_id=fx_cfg["id"],
                name=fx_cfg["name"],
                dmx_address=fx_cfg["dmx_address"],
                lane=fx_cfg.get("lane", "room"),
                group=fx_cfg.get("group", "all"),
            ))
    if not fixtures:
        print("[ERROR] No fixtures found in rig_config.json")
        sys.exit(1)

    # ---- DMX output ----
    if args.serial:
        try:
            dmx_out = EnttecProOutput()
            dmx_out.open(args.serial)
            print(f"[DMX] Opened serial port: {args.serial}")
        except Exception as e:
            print(f"[DMX] Serial open failed ({e}), falling back to MOCK")
            dmx_out = MockDMXOutput(verbose=args.verbose_dmx)
    else:
        dmx_out = MockDMXOutput(verbose=args.verbose_dmx)

    # ---- MIDI ----
    midi_in = None
    if MidiInput is not None:
        midi_in = MidiInput(port_name=args.midi)
        if midi_in.open():
            print(f"[MIDI] Listening on: {MidiInput.list_ports()[:1]}")
        else:
            midi_in = None   # graceful degradation

    # ---- scene presets ----
    scene_mgr = SceneManager(SCENES_DIR, POSITIONS_FILE, STATES_FILE)
    scene_mgr.load_all()
    _all_scenes = scene_mgr.list_scenes()

    # ---- UI ----
    overlay = TerminalDebugOverlay()
    overlay.init_screen()

    # ---- keyboard thread ----
    kbd_thread = _start_keyboard()

    # Sprint 2: WAU crossfade snapshot (captured at each mode switch)
    _wau_snapshot = (0.0, 0.0, 0.0)

    last_bands = AudioBands()
    last_lanes: dict = {"impact": 0.0, "room": 0.0}
    quit_flag  = False

    try:
        while not quit_flag:
            frame_start = time.monotonic()

            # --- keyboard ---
            while not _key_queue.empty():
                key = _key_queue.get_nowait()
                if key == "q" or key == "\x03":
                    quit_flag = True
                    break
                elif key == " ":
                    safety.toggle_blackout()
                elif key in _SCENE_FKEYS:
                    idx = int(key[1:]) - 1  # F1→0 … F9→8
                    if idx < len(_all_scenes):
                        scene = _all_scenes[idx]
                        scene_mgr.activate_scene(scene.scene_id)
                        print(f"[Scene] {key}: {scene.name}")
                        # Switch to the scene's base mode
                        base_mk = scene.base_mode
                        if base_mk in MODES:
                            new_mode    = get_mode(base_mk)
                            new_palette = all_palettes.get(
                                new_mode.palette_key, current_palette
                            )
                            _wau_snapshot = (last_lanes.get("wau_white", 0.0),
                                             last_lanes.get("wau_amber", 0.0),
                                             last_lanes.get("wau_uv",    0.0))
                            transitioner.switch(new_mode)
                            current_mode    = new_mode
                            current_palette = new_palette
                            safety.update_from_mode(current_mode)
                            room_lane.set_mode(current_mode)
                            room_lane.set_palette(current_palette)

                elif key == "F10":
                    scene_mgr.release_scene()
                    print("[Scene] Released — back to mode engine")

                elif key in KEYBOARD_MAP:
                    mode_key = KEYBOARD_MAP[key]
                    if mode_key == "quit":
                        quit_flag = True
                        break
                    elif mode_key == "blackout":
                        safety.toggle_blackout()
                    else:
                        new_mode    = get_mode(mode_key)
                        new_palette = all_palettes.get(
                            new_mode.palette_key, current_palette
                        )
                        _wau_snapshot = (last_lanes.get("wau_white", 0.0),
                                         last_lanes.get("wau_amber", 0.0),
                                         last_lanes.get("wau_uv",    0.0))
                        transitioner.switch(new_mode)
                        current_mode    = new_mode
                        current_palette = new_palette
                        safety.update_from_mode(current_mode)
                        room_lane.set_mode(current_mode)
                        room_lane.set_palette(current_palette)

            # --- MIDI ---
            if midi_in is not None:
                for evt in midi_in.get_events():
                    if evt.type == "mode":
                        new_mode    = get_mode(evt.value)
                        new_palette = all_palettes.get(new_mode.palette_key, current_palette)
                        _wau_snapshot = (last_lanes.get("wau_white", 0.0),
                                         last_lanes.get("wau_amber", 0.0),
                                         last_lanes.get("wau_uv",    0.0))
                        transitioner.switch(new_mode)
                        current_mode    = new_mode
                        current_palette = new_palette
                        safety.update_from_mode(current_mode)
                        room_lane.set_mode(current_mode)
                        room_lane.set_palette(current_palette)
                    elif evt.type == "dimmer":
                        pass  # TODO: route to master_dimmer
                    elif evt.type == "blackout":
                        if evt.value > 0.5:
                            if not safety.state.blackout_active:
                                safety.toggle_blackout()
                        else:
                            if safety.state.blackout_active:
                                safety.toggle_blackout()

            if quit_flag:
                break

            # --- audio ---
            block = capture.get_latest_block()
            if block is not None:
                last_bands = analyzer.analyze(block)
            else:
                last_bands = AudioBands()

            bands_dict = last_bands.as_dict()

            # --- beat detection ---
            beat_detected, beat_strength = beat_detector.update(
                bands_dict.get("low_energy", 0.0)
            )

            # --- smoothing ---
            last_lanes = smoother.update(bands_dict)
            last_lanes["bpm"]  = beat_detector.bpm
            last_lanes["beat"] = beat_detected

            # --- lane render ---
            room_out = room_lane.render(
                smoothed_room=last_lanes["room"],
                impact=last_lanes["impact"],
                safety=safety,
                master_dimmer=1.0,
                group_intensity=1.0,
                beat_trigger=beat_detected,
            )

            # --- Sprint 2: WAU crossfade ---
            blend_t = transitioner.update()
            if blend_t < 1.0:
                sw, sa, su = _wau_snapshot
                eff_white = sw + (room_out.white - sw) * blend_t
                eff_amber = sa + (room_out.amber - sa) * blend_t
                eff_uv    = su + (room_out.uv    - su) * blend_t
            else:
                eff_white, eff_amber, eff_uv = room_out.white, room_out.amber, room_out.uv

            # Persist for next-frame snapshot
            last_lanes["wau_white"] = eff_white
            last_lanes["wau_amber"] = eff_amber
            last_lanes["wau_uv"]    = eff_uv

            # --- scene colour override (priority: scene > mode engine) ---
            _scene_ov = scene_mgr.get_uplight_color_override()
            if _scene_ov is not None:
                _sc_rgb, _sc_brt, _sc_reactive = _scene_ov
                _r_f = _sc_rgb[0] / 255.0
                _g_f = _sc_rgb[1] / 255.0
                _b_f = _sc_rgb[2] / 255.0
                import colorsys as _cs2
                _sh, _ss, _sv = _cs2.rgb_to_hsv(_r_f, _g_f, _b_f)
                _render_h = _sh * 360.0
                _render_s = _ss
                _render_v = room_out.hsv.v if _sc_reactive else _sc_brt
            else:
                _render_h = room_out.hsv.h
                _render_s = room_out.hsv.s
                _render_v = room_out.hsv.v

            # --- fixture write (all fixtures get same lane output) ---
            for fixture in fixtures:
                fixture.render_to_universe(
                    universe,
                    brightness=1.0,
                    hue=_render_h,
                    saturation=_render_s,
                    value=_render_v,
                    strobe=room_out.strobe,
                    white=eff_white,
                    amber=eff_amber,
                    uv=eff_uv,
                )

            # --- DMX send ---
            dmx_out.send(universe)

            # --- overlay prep ---
            h_norm = room_out.hsv.h / 360.0
            r_f, g_f, b_f = colorsys.hsv_to_rgb(
                h_norm, room_out.hsv.s, room_out.hsv.v
            )
            rgb_out = (int(r_f * 255), int(g_f * 255), int(b_f * 255))

            primary_fixture = fixtures[0]
            ch_labels       = primary_fixture.get_channel_labels()
            dmx_ch_vals     = {
                label: universe.get_channel(ch)
                for label, ch in ch_labels.items()
            }

            _active_scene = scene_mgr.active_scene
            _mode_display = (
                f"{current_mode.display_name} [{_active_scene.name}]"
                if _active_scene else current_mode.display_name
            )

            overlay.update(
                device_name=device_label,
                raw_bands=bands_dict,
                smoothed_lanes=last_lanes,
                mode_name=_mode_display,
                palette_name=room_lane.palette_name,
                color_name=room_lane.current_color_name,
                next_color_name=room_lane.next_color_name,
                hold_remaining_ms=room_lane.hold_remaining_ms,
                transition_progress=room_lane.transition_progress,
                hsv=(room_out.hsv.h, room_out.hsv.s, room_out.hsv.v),
                rgb=rgb_out,
                brightness_base=room_out.base_brightness,
                brightness_pulse=room_out.pulse_brightness,
                fixture_name=primary_fixture.name,
                dmx_address=primary_fixture.dmx_address,
                dmx_channels=dmx_ch_vals,
                dmx_output_type=dmx_out.output_type,
                safety_blackout=safety.state.blackout_active,
                safety_strobe_ok=safety.state.strobe_allowed,
                error=error_msg or getattr(capture, "last_error", None),
            )

            # --- frame rate cap ---
            elapsed = time.monotonic() - frame_start
            sleep_t = FRAME_TIME - elapsed
            if sleep_t > 0:
                time.sleep(sleep_t)

    except KeyboardInterrupt:
        pass
    finally:
        _stop_keyboard(kbd_thread)
        if midi_in is not None:
            midi_in.close()
        universe.blackout()
        dmx_out.send(universe)
        dmx_out.close()
        capture.stop()
        overlay.restore_screen()
        print("LightBrain stopped.")


if __name__ == "__main__":
    main()
