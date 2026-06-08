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
from dmx.output_artnet     import ArtNetOutput
from dmx.output_thread     import DmxOutputThread
from engine.pacer          import precise_sleep_until

from fixtures.rockwedge import RockWedge
from fixtures.chauvet_wash_fx2 import ChauvetWashFX2
from fixtures.chauvet_gigbar_move_ils import ChauvetGigBarMoveILS
from fixtures.fixture import check_dmx_address_map

from ui.terminal_debug import TerminalDebugOverlay
from engine.scenes        import SceneManager
from engine.strobe        import StrobeEngine
from engine.hue_crossfader import HueCrossfader
from app.render.scene import SceneLayout
from app.web import server as _web
from app.web import ipad_server as _ipad

try:
    from midi.input import MidiInput
except ImportError:
    MidiInput = None


# ---- constants ----
CONFIG_PATH     = os.path.join(ROOT, "config", "rig_config.json")
APP_CONFIG_PATH = os.path.join(ROOT, "config", "app_config.json")
PALETTES_DIR    = os.path.join(ROOT, "config", "palettes")
SCENES_DIR      = os.path.join(ROOT, "config", "scenes")
POSITIONS_FILE  = os.path.join(ROOT, "fixtures", "positions.json")
STATES_FILE     = os.path.join(ROOT, "fixtures", "states.json")
BLACKOUT_FADE_S = 0.8      # seconds to fade out when blackout is activated

# Fixed render params for each fixture test pattern (sent as-is to render_to_universe)
_TEST_PATTERNS: dict = {
    "blackout": dict(brightness=0.0, hue=0.0,   saturation=0.0, value=0.0, strobe=0.0, white=0.0, amber=0.0, uv=0.0),
    "dim":      dict(brightness=0.1, hue=0.0,   saturation=0.0, value=1.0, strobe=0.0, white=1.0, amber=0.0, uv=0.0),
    "white":    dict(brightness=1.0, hue=0.0,   saturation=0.0, value=1.0, strobe=0.0, white=1.0, amber=0.0, uv=0.0),
    "red":      dict(brightness=1.0, hue=0.0,   saturation=1.0, value=1.0, strobe=0.0, white=0.0, amber=0.0, uv=0.0),
    "green":    dict(brightness=1.0, hue=120.0, saturation=1.0, value=1.0, strobe=0.0, white=0.0, amber=0.0, uv=0.0),
    "blue":     dict(brightness=1.0, hue=240.0, saturation=1.0, value=1.0, strobe=0.0, white=0.0, amber=0.0, uv=0.0),
    "yellow":   dict(brightness=1.0, hue=60.0,  saturation=1.0, value=1.0, strobe=0.0, white=0.0, amber=0.0, uv=0.0),
    "cyan":     dict(brightness=1.0, hue=180.0, saturation=1.0, value=1.0, strobe=0.0, white=0.0, amber=0.0, uv=0.0),
    "magenta":  dict(brightness=1.0, hue=300.0, saturation=1.0, value=1.0, strobe=0.0, white=0.0, amber=0.0, uv=0.0),
    "uv":       dict(brightness=1.0, hue=0.0,   saturation=0.0, value=0.0, strobe=0.0, white=0.0, amber=0.0, uv=1.0),
    "amber":    dict(brightness=1.0, hue=0.0,   saturation=0.0, value=0.0, strobe=0.0, white=0.0, amber=1.0, uv=0.0),
    "strobe":   dict(brightness=1.0, hue=0.0,   saturation=0.0, value=1.0, strobe=1.0, white=1.0, amber=0.0, uv=0.0),
}


def _load_app_config() -> dict:
    if os.path.exists(APP_CONFIG_PATH):
        with open(APP_CONFIG_PATH) as f:
            return json.load(f)
    return {"web_server_enabled": True, "web_server_port": 8080, "headless_mode": False}

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
    except Exception as e:
        import logging
        logging.getLogger(__name__).error("Keyboard thread died: %s", e)


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
                        help="serial port for Enttec USB Pro (e.g. /dev/ttyUSB0)")
    parser.add_argument("--artnet",      type=str,  default=None,
                        help="Art-Net target IP (e.g. 2.0.0.1 or 2.255.255.255)")
    parser.add_argument("--verbose-dmx", action="store_true",
                        help="print every changed DMX channel to console")
    parser.add_argument("--midi",        type=str,  default=None,
                        help="MIDI input port name (default: first available)")
    parser.add_argument("--web",         action="store_true",
                        help="start web dashboard (requires fastapi + uvicorn)")
    parser.add_argument("--web-port",    type=int,  default=8765,
                        help="web dashboard port (default: 8765)")
    parser.add_argument("--ipad-port",   type=int,  default=None,
                        help="iPad controller port (default: from app_config.json)")
    parser.add_argument("--headless",    action="store_true",
                        help="headless mode — no terminal overlay, no keyboard input")
    args = parser.parse_args()

    app_cfg = _load_app_config()
    if args.headless or app_cfg.get("headless_mode", False):
        args.headless = True

    config = load_config()

    # ---- fps / frame timing (from config, overridable) ----
    _target_fps = int(config.get("target_fps", 40))
    _frame_time = 1.0 / max(1, _target_fps)

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
        channels     = config["audio"].get("channels", 1)
        capture      = AudioCapture(device_index, sample_rate, block_size, channels)
        try:
            capture.start()
            device_label = capture.device_name or "unknown device"
        except Exception as e:
            error_msg    = f"Audio start failed: {e}"
            device_label = "no device"

    analyzer     = AudioAnalyzer()
    beat_detector = BeatDetector()

    # ---- engine ----
    smoother    = LaneSmoother(mode_key)
    safety      = SafetyEngine()
    safety.update_from_mode(current_mode)
    room_lane   = RoomLane(current_palette, mode=current_mode)
    transitioner = ModeTransitioner(current_mode)

    # ---- fixtures ----
    universe  = DMXUniverse()
    fixtures  = []
    for fx_cfg in config.get("fixtures", []):
        _ftype = fx_cfg.get("type")
        _fid   = fx_cfg["id"]
        _fname = fx_cfg["name"]
        _faddr = fx_cfg["dmx_address"]
        _flane = fx_cfg.get("lane", "room")
        _fgrp  = fx_cfg.get("group", "all")
        if _ftype == "rockwedge":
            fixtures.append(RockWedge(
                fixture_id=_fid, name=_fname, dmx_address=_faddr,
                lane=_flane, group=_fgrp,
            ))
        elif _ftype == "wash_fx2":
            fixtures.append(ChauvetWashFX2(
                fixture_id=_fid, name=_fname, dmx_address=_faddr,
                lane=_flane, group=_fgrp,
            ))
        elif _ftype == "gigbar_move_ils":
            fixtures.append(ChauvetGigBarMoveILS(
                fixture_id=_fid, name=_fname, dmx_address=_faddr,
                lane=_flane, group=_fgrp,
                spot_pan_deg=float(fx_cfg.get("spot_pan_deg", 270.0)),
                spot_tilt_dmx=int(fx_cfg.get("spot_tilt_dmx", 90)),
                laser_enabled=bool(fx_cfg.get("laser_enabled", False)),
            ))
    if not fixtures:
        print("[ERROR] No fixtures found in rig_config.json")
        sys.exit(1)

    try:
        check_dmx_address_map(fixtures)
    except ValueError as e:
        print(f"[ERROR] DMX address conflict in rig_config.json:\n{e}")
        sys.exit(1)

    _web.set_rig_layout(fixtures)

    # ---- DMX output backend ----
    # Priority: --serial CLI > --artnet CLI > config dmx.output > mock
    _dmx_cfg    = config.get("dmx", {})
    _cfg_output = _dmx_cfg.get("output", "mock")
    _cfg_port   = _dmx_cfg.get("serial_port") or None

    _serial_port = args.serial or (_cfg_port if _cfg_output == "enttec" else None)
    _artnet_ip   = args.artnet or (_dmx_cfg.get("artnet_ip") if _cfg_output == "artnet" else None)

    if _serial_port:
        try:
            _dmx_backend = EnttecProOutput()
            _dmx_backend.open(_serial_port)
            print(f"[DMX] Opened Enttec on {_serial_port}")
        except Exception as e:
            print(f"[DMX] Serial open failed ({e}), falling back to MOCK")
            _dmx_backend = MockDMXOutput(verbose=args.verbose_dmx)
    elif _artnet_ip:
        class _ArtNetAdapter:
            """Thin adapter: gives ArtNetOutput the send(universe)/close() interface."""
            output_type = "artnet"
            def __init__(self, ip: str) -> None:
                self._out = ArtNetOutput(target_ip=ip)
                self._out.connect()
                print(f"[DMX] Art-Net → {ip}")
            def send(self, universe) -> None:
                self._out.send_universe(list(universe.to_bytes()))
            def close(self) -> None:
                self._out.disconnect()
        _dmx_backend = _ArtNetAdapter(_artnet_ip)
    else:
        _dmx_backend = MockDMXOutput(verbose=args.verbose_dmx)

    # ---- DMX output thread ----
    # The backend runs in its own 40Hz daemon thread — pyserial.write() never
    # touches the DSP critical path.
    dmx_thread = DmxOutputThread(_dmx_backend, fps=_target_fps)
    dmx_thread.start()

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
    scene_layout   = SceneLayout()
    strobe_engine  = StrobeEngine()
    hue_crossfader = HueCrossfader(duration_s=0.5)

    # ---- web dashboard ----
    if args.web:
        _web.set_catalog(
            modes  = [{"key": m.key, "display_name": m.display_name}
                      for m in [get_mode(k) for k in MODES if k != "blackout"]],
            scenes = [{"id": s.scene_id, "name": s.name, "index": i}
                      for i, s in enumerate(_all_scenes)],
        )
        _web.start(port=args.web_port)
        _web.set_paths(SCENES_DIR, POSITIONS_FILE, STATES_FILE, scene_mgr)

    # ---- iPad controller ----
    _ipad_enabled = app_cfg.get("web_server_enabled", True)
    _ipad_port    = args.ipad_port or app_cfg.get("web_server_port", 8080)
    _ipad_token   = app_cfg.get("web_server_token", "")
    if _ipad_enabled:
        _ipad.start(port=_ipad_port, token=_ipad_token)

    # ---- fps counter ----
    _fps_frames = 0
    _fps_last_t = time.monotonic()
    _fps_display = 0

    # ---- UI ----
    overlay = None
    kbd_thread = None
    if not args.headless:
        overlay = TerminalDebugOverlay()
        overlay.init_screen()
        kbd_thread = _start_keyboard()
    else:
        print("[Headless] Terminal overlay and keyboard disabled.")

    # Sprint 2: WAU crossfade snapshot (captured at each mode switch)
    _wau_snapshot = (0.0, 0.0, 0.0)

    last_bands = AudioBands()
    last_lanes: dict = {"impact": 0.0, "room": 0.0}
    quit_flag          = False
    _last_hue          = 0.0    # raw hue from previous frame — reference for snap()
    _prev_mode_key     = mode_key
    _strobe_master     = 1.0    # 0–1 multiplier, set via web/iPad slider
    _master_dimmer     = float(config.get("master_dimmer", 1.0))  # 0–1, overall brightness
    _uplight_dimmer    = 1.0    # 0–1, uplight-only brightness
    _flash_frames      = 0      # countdown frames for manual flash hit
    _strobe_burst_end  = 0.0    # monotonic time when strobe burst expires
    _strobe_armed      = False   # latching arm toggle — strobe runs until disarmed
    _strobe_hold       = False   # iPad hold-to-strobe button state
    _strobe_hold_phase = 0.0    # software oscillator phase for strobe hold
    _dmx_snapshot      = [0] * 512  # last rendered universe for Rig tab
    _test_mode         = False   # fixture test mode — overrides audio engine
    _test_pattern      = ""      # active test pattern name
    _blackout_fading   = False   # True while blackout fade-out is in progress
    _blackout_fade_start = 0.0   # monotonic time when fade began
    _blackout_fade_alpha = 1.0   # 1.0=full, 0.0=dark
    _fade_v_snap       = 0.0    # _render_v snapshot at fade start
    _fade_brt_snap     = 1.0    # _master_dimmer snapshot at fade start
    _fade_w_snap       = 0.0    # eff_white snapshot at fade start
    _fade_a_snap       = 0.0    # eff_amber snapshot at fade start
    _fade_uv_snap      = 0.0    # eff_uv snapshot at fade start
    _last_render_v     = 0.0    # end-of-frame _render_v for next snapshot
    _last_eff_white    = 0.0
    _last_eff_amber    = 0.0
    _last_eff_uv       = 0.0
    _kill_strobe       = False   # kill switch: silence all strobe output
    _kill_derby        = False   # kill switch: stop derby rotation/color
    _kill_laser        = False   # kill switch: force laser off

    try:
        while not quit_flag:
            frame_start = time.monotonic()
            _prev_mode_key = mode_key   # capture before input handling changes it

            # --- keyboard (disabled in headless mode) ---
            while not args.headless and not _key_queue.empty():
                key = _key_queue.get_nowait()
                if key == "q" or key == "\x03":
                    quit_flag = True
                    break
                elif key == " ":
                    _was_bo = safety.state.blackout_active
                    safety.toggle_blackout()
                    if not _was_bo:
                        _blackout_fading   = True
                        _blackout_fade_start = time.monotonic()
                        _blackout_fade_alpha = 1.0
                        _fade_v_snap   = _last_render_v
                        _fade_brt_snap = _master_dimmer
                        _fade_w_snap   = _last_eff_white
                        _fade_a_snap   = _last_eff_amber
                        _fade_uv_snap  = _last_eff_uv
                    else:
                        _blackout_fading   = False
                        _blackout_fade_alpha = 1.0
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
                            mode_key        = base_mk
                            safety.update_from_mode(current_mode)
                            room_lane.set_mode(current_mode)
                            room_lane.set_palette(current_palette)
                            smoother.apply_mode_profile(mode_key)

                elif key == "F10":
                    scene_mgr.release_scene()
                    print("[Scene] Released — back to mode engine")

                elif key in KEYBOARD_MAP:
                    mode_key = KEYBOARD_MAP[key]
                    if mode_key == "quit":
                        quit_flag = True
                        break
                    elif mode_key == "blackout":
                        _was_bo = safety.state.blackout_active
                        safety.toggle_blackout()
                        if not _was_bo:
                            _blackout_fading   = True
                            _blackout_fade_start = time.monotonic()
                            _blackout_fade_alpha = 1.0
                            _fade_v_snap   = _last_render_v
                            _fade_brt_snap = _master_dimmer
                            _fade_w_snap   = _last_eff_white
                            _fade_a_snap   = _last_eff_amber
                            _fade_uv_snap  = _last_eff_uv
                        else:
                            _blackout_fading   = False
                            _blackout_fade_alpha = 1.0
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
                        smoother.apply_mode_profile(mode_key)

            # --- web commands ---
            for _wcmd in _web.get_all_commands():
                _wtype = _wcmd.get("type", "")
                if _wtype == "mode":
                    _wval = _wcmd.get("value", "")
                    if _wval in MODES:
                        new_mode    = get_mode(_wval)
                        new_palette = all_palettes.get(
                            new_mode.palette_key, current_palette
                        )
                        _wau_snapshot = (last_lanes.get("wau_white", 0.0),
                                         last_lanes.get("wau_amber", 0.0),
                                         last_lanes.get("wau_uv",    0.0))
                        transitioner.switch(new_mode)
                        current_mode    = new_mode
                        current_palette = new_palette
                        mode_key        = _wval
                        safety.update_from_mode(current_mode)
                        room_lane.set_mode(current_mode)
                        room_lane.set_palette(current_palette)
                        smoother.apply_mode_profile(mode_key)
                elif _wtype == "scene":
                    _sid = _wcmd.get("value", "")
                    if scene_mgr.activate_scene(_sid):
                        _base = scene_mgr.active_base_mode
                        if _base and _base in MODES:
                            new_mode    = get_mode(_base)
                            new_palette = all_palettes.get(
                                new_mode.palette_key, current_palette
                            )
                            _wau_snapshot = (last_lanes.get("wau_white", 0.0),
                                             last_lanes.get("wau_amber", 0.0),
                                             last_lanes.get("wau_uv",    0.0))
                            transitioner.switch(new_mode)
                            current_mode    = new_mode
                            current_palette = new_palette
                            mode_key        = _base
                            safety.update_from_mode(current_mode)
                            room_lane.set_mode(current_mode)
                            room_lane.set_palette(current_palette)
                            smoother.apply_mode_profile(mode_key)
                elif _wtype == "release_scene":
                    scene_mgr.release_scene()
                elif _wtype == "blackout":
                    _was_bo = safety.state.blackout_active
                    safety.toggle_blackout()
                    if not _was_bo:
                        _blackout_fading   = True
                        _blackout_fade_start = time.monotonic()
                        _blackout_fade_alpha = 1.0
                        _fade_v_snap   = _last_render_v
                        _fade_brt_snap = _master_dimmer
                        _fade_w_snap   = _last_eff_white
                        _fade_a_snap   = _last_eff_amber
                        _fade_uv_snap  = _last_eff_uv
                    else:
                        _blackout_fading   = False
                        _blackout_fade_alpha = 1.0
                elif _wtype == "strobe_master":
                    try:
                        _strobe_master = min(1.0, max(0.0, float(_wcmd.get("value", 1.0))))
                    except (ValueError, TypeError):
                        pass
                elif _wtype == "set_mode":
                    _wval = _wcmd.get("value", "")
                    if _wval in MODES:
                        new_mode    = get_mode(_wval)
                        new_palette = all_palettes.get(
                            new_mode.palette_key, current_palette
                        )
                        _wau_snapshot = (last_lanes.get("wau_white", 0.0),
                                         last_lanes.get("wau_amber", 0.0),
                                         last_lanes.get("wau_uv",    0.0))
                        transitioner.switch(new_mode)
                        current_mode    = new_mode
                        current_palette = new_palette
                        mode_key        = _wval
                        safety.update_from_mode(current_mode)
                        room_lane.set_mode(current_mode)
                        room_lane.set_palette(current_palette)
                        smoother.apply_mode_profile(mode_key)
                elif _wtype == "activate_scene":
                    _sid = _wcmd.get("value", "")
                    if scene_mgr.activate_scene(_sid):
                        _base = scene_mgr.active_base_mode
                        if _base and _base in MODES:
                            new_mode    = get_mode(_base)
                            new_palette = all_palettes.get(
                                new_mode.palette_key, current_palette
                            )
                            _wau_snapshot = (last_lanes.get("wau_white", 0.0),
                                             last_lanes.get("wau_amber", 0.0),
                                             last_lanes.get("wau_uv",    0.0))
                            transitioner.switch(new_mode)
                            current_mode    = new_mode
                            current_palette = new_palette
                            mode_key        = _base
                            safety.update_from_mode(current_mode)
                            room_lane.set_mode(current_mode)
                            room_lane.set_palette(current_palette)
                            smoother.apply_mode_profile(mode_key)
                elif _wtype == "set_fader":
                    _fname = _wcmd.get("fader", "")
                    try:
                        _fval = min(1.0, max(0.0, float(_wcmd.get("value", 1.0))))
                    except (ValueError, TypeError):
                        continue
                    if _fname == "master":
                        _master_dimmer = _fval
                    elif _fname == "uplight":
                        _uplight_dimmer = _fval
                    elif _fname == "strobe":
                        _strobe_master = _fval
                elif _wtype == "momentary":
                    _eff = _wcmd.get("effect", "")
                    _act = _wcmd.get("action", "start")
                    if _eff == "flash" and _act == "start":
                        _flash_frames = 3
                    elif _eff == "strobe_burst" and _act == "start":
                        _strobe_burst_end = time.monotonic() + 2.0
                    elif _eff == "strobe_hold":
                        _strobe_hold = (_act == "start")
                elif _wtype == "arm_strobe":
                    _strobe_armed = not _strobe_armed
                elif _wtype == "toggle_kill":
                    _ktarget = _wcmd.get("target", "")
                    if _ktarget == "strobe":
                        _kill_strobe = not _kill_strobe
                    elif _ktarget == "derby":
                        _kill_derby = not _kill_derby
                    elif _ktarget == "laser":
                        _kill_laser = not _kill_laser
                elif _wtype == "fixture_test":
                    _pname = _wcmd.get("pattern", "white")
                    if _pname in _TEST_PATTERNS:
                        _test_mode    = True
                        _test_pattern = _pname
                elif _wtype == "release_fixture_test":
                    _test_mode    = False
                    _test_pattern = ""
                elif _wtype == "fixture_test_aim":
                    try:
                        _aim_pan  = float(_wcmd.get("pan",  270.0))
                        _aim_tilt = int(_wcmd.get("tilt", 90))
                        for _fx in fixtures:
                            if hasattr(_fx, "set_spot_aim"):
                                _fx.set_spot_aim(_aim_pan, _aim_tilt)
                    except (ValueError, TypeError):
                        pass

            # --- MIDI ---
            if midi_in is not None:
                for evt in midi_in.get_events():
                    if evt.type == "mode" and evt.value in MODES:
                        new_mode    = get_mode(evt.value)
                        new_palette = all_palettes.get(new_mode.palette_key, current_palette)
                        _wau_snapshot = (last_lanes.get("wau_white", 0.0),
                                         last_lanes.get("wau_amber", 0.0),
                                         last_lanes.get("wau_uv",    0.0))
                        transitioner.switch(new_mode)
                        current_mode    = new_mode
                        current_palette = new_palette
                        mode_key        = evt.value
                        safety.update_from_mode(current_mode)
                        room_lane.set_mode(current_mode)
                        room_lane.set_palette(current_palette)
                        smoother.apply_mode_profile(mode_key)
                    elif evt.type == "dimmer":
                        pass  # TODO: route to master_dimmer
                    elif evt.type == "blackout":
                        if evt.value > 0.5:
                            if not safety.state.blackout_active:
                                safety.toggle_blackout()
                                _blackout_fading   = True
                                _blackout_fade_start = time.monotonic()
                                _blackout_fade_alpha = 1.0
                                _fade_v_snap   = _last_render_v
                                _fade_brt_snap = _master_dimmer
                                _fade_w_snap   = _last_eff_white
                                _fade_a_snap   = _last_eff_amber
                                _fade_uv_snap  = _last_eff_uv
                        else:
                            if safety.state.blackout_active:
                                safety.toggle_blackout()
                                _blackout_fading   = False
                                _blackout_fade_alpha = 1.0

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
                _sh, _ss, _sv = colorsys.rgb_to_hsv(_r_f, _g_f, _b_f)
                _render_h = _sh * 360.0
                _render_s = _ss
                _render_v = room_out.hsv.v if _sc_reactive else _sc_brt
            else:
                _render_h = room_out.hsv.h
                _render_s = room_out.hsv.s
                _render_v = room_out.hsv.v

            # --- strobe engine (EDM lift) ---
            _now = time.monotonic()
            _strobe_on, _strobe_rate, _ = strobe_engine.update(
                high_energy=bands_dict.get("high_energy", 0.0),
                mode_key=mode_key,
                now=_now,
            )
            _eff_strobe = _strobe_rate * _strobe_master if safety.state.strobe_allowed else 0.0

            # --- strobe burst override (DJ manual override — bypasses strobe_allowed) ---
            if time.monotonic() < _strobe_burst_end and not safety.state.blackout_active:
                _eff_strobe = _strobe_master

            # --- strobe hold (iPad hold-to-strobe — software oscillator) ---
            if (_strobe_hold or _strobe_armed) and not safety.state.blackout_active:
                _hold_freq = 2.0 + _strobe_master * 14.0
                _strobe_hold_phase = (_strobe_hold_phase + _frame_time * _hold_freq) % 1.0
                _eff_strobe = _strobe_master
            else:
                _strobe_hold_phase = 0.0

            # --- prepare per-frame render values ---
            _frame_brt = _master_dimmer
            _frame_h   = _render_h
            _frame_s   = _render_s
            _frame_v   = _render_v
            _frame_w   = eff_white

            # snapshot clean values each frame so blackout fade can start from them
            _last_render_v  = _render_v
            _last_eff_white = eff_white
            _last_eff_amber = eff_amber
            _last_eff_uv    = eff_uv

            # --- strobe hold flicker (software side — toggles brightness) ---
            if (_strobe_hold or _strobe_armed) and not safety.state.blackout_active:
                if _strobe_hold_phase >= 0.25:
                    _frame_v   = 0.0
                    _frame_w   = 0.0
                    _frame_brt = 0.0
                else:
                    _frame_v   = 1.0
                    _frame_w   = 1.0
                    _frame_brt = 1.0

            if _flash_frames > 0 and not safety.state.blackout_active:
                _flash_frames -= 1
                _frame_brt = 1.0
                _frame_v   = 1.0
                _frame_s   = 0.0
                _frame_w   = 1.0
            elif _flash_frames > 0:
                _flash_frames = 0

            # --- blackout fade override ---
            if _blackout_fading:
                _elapsed_fade = time.monotonic() - _blackout_fade_start
                _blackout_fade_alpha = max(0.0, 1.0 - _elapsed_fade / BLACKOUT_FADE_S)
                if _blackout_fade_alpha <= 0.0:
                    _blackout_fading = False
                _frame_v    = _fade_v_snap   * _blackout_fade_alpha
                _frame_brt  = _fade_brt_snap * _blackout_fade_alpha
                _frame_w    = _fade_w_snap   * _blackout_fade_alpha
                eff_amber   = _fade_a_snap   * _blackout_fade_alpha
                eff_uv      = _fade_uv_snap  * _blackout_fade_alpha
                _eff_strobe = 0.0

            # --- fixture test override (bypasses audio engine) ---
            if _test_mode and not safety.state.blackout_active:
                _tp         = _TEST_PATTERNS[_test_pattern]
                _frame_brt  = _tp["brightness"]
                _frame_h    = _tp["hue"]
                _frame_s    = _tp["saturation"]
                _frame_v    = _tp["value"]
                _frame_w    = _tp["white"]
                eff_amber   = _tp["amber"]
                eff_uv      = _tp["uv"]
                _eff_strobe = _tp["strobe"]

            # --- kill switch overrides (last word before fixture write) ---
            if _kill_strobe:
                _eff_strobe = 0.0
            for _fx in fixtures:
                if hasattr(_fx, "set_derby_enabled"):
                    _fx.set_derby_enabled(not _kill_derby)
                if hasattr(_fx, "enable_laser"):
                    _fx.enable_laser(not _kill_laser)

            # --- fixture write (all fixtures get same lane output) ---
            for fixture in fixtures:
                fixture.render_to_universe(
                    universe,
                    brightness=_frame_brt,
                    hue=_frame_h,
                    saturation=_frame_s,
                    value=_frame_v,
                    strobe=_eff_strobe,
                    white=_frame_w,
                    amber=eff_amber,
                    uv=eff_uv,
                )

            # --- universe-level blackout guard (skip while fade is in progress) ---
            if safety.state.blackout_active and not _blackout_fading:
                universe.blackout()

            # --- post to DMX thread (non-blocking ~1µs copy into single-slot buffer) ---
            dmx_thread.post(universe)
            _dmx_snapshot = universe._channels.tolist()

            # --- overlay prep (skip in headless) ---
            if overlay:
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
                    dmx_output_type=dmx_thread.output_type,
                    safety_blackout=safety.state.blackout_active,
                    safety_strobe_ok=safety.state.strobe_allowed,
                    error=error_msg or getattr(capture, "last_error", None),
                )

            # --- fps counter ---
            _fps_frames += 1
            _fps_now = time.monotonic()
            if _fps_now - _fps_last_t >= 1.0:
                _fps_display = _fps_frames
                _fps_frames  = 0
                _fps_last_t  = _fps_now

            # --- web state push (dashboard + iPad both read _engine_state) ---
            if args.web or _ipad_enabled:
                # Snap hue crossfader on mode change (before blend)
                _raw_hue = room_out.hsv.h
                if _prev_mode_key != mode_key:
                    hue_crossfader.snap(_last_hue, instant=current_mode.transition_snap)
                _display_hue = hue_crossfader.blend(_raw_hue)
                _last_hue = _raw_hue   # update reference for next frame

                _active_s = scene_mgr.active_scene
                _rig_web = scene_layout.update_and_build(
                    bands=bands_dict, lanes=last_lanes,
                    hue=_display_hue, saturation=room_out.hsv.s,
                    brightness=room_out.hsv.v,
                    base_brt=room_out.base_brightness,
                    pulse_brt=room_out.pulse_brightness,
                    mode_key=mode_key, palette_name=room_lane.palette_name,
                    blackout=safety.state.blackout_active,
                    strobe_on=_strobe_on, strobe_rate=_eff_strobe,
                    ambient_white=eff_white, ambient_amber=eff_amber,
                )
                _rig_web = scene_mgr.apply_to_rig_state(_rig_web)
                if _uplight_dimmer < 1.0:
                    for _ul in _rig_web.uplights:
                        _ul.brightness *= _uplight_dimmer
                _web.update_state(
                    mode=            mode_key,
                    mode_display=    current_mode.display_name,
                    scene=           scene_mgr.active_scene_id,
                    scene_name=      _active_s.name if _active_s else "",
                    blackout=        safety.state.blackout_active,
                    bpm=             float(last_lanes.get("bpm", 0.0)),
                    beat=            bool(last_lanes.get("beat", False)),
                    low_energy=      float(bands_dict.get("low_energy",    0.0)),
                    mid_energy=      float(bands_dict.get("mid_energy",    0.0)),
                    high_energy=     float(bands_dict.get("high_energy",   0.0)),
                    overall_energy=  float(bands_dict.get("overall_energy",0.0)),
                    fps=             _fps_display,
                    dmx_output=      dmx_thread.output_type,
                    fixtures=        _web.serialize_rig_state(_rig_web),
                    impact_lane=     float(last_lanes.get("impact", 0.0)),
                    room_lane=       float(last_lanes.get("room",   0.0)),
                    strobe_rate=     float(_eff_strobe),
                    strobe_master=   float(_strobe_master),
                    master_dimmer=   float(_master_dimmer),
                    uplight_dimmer=  float(_uplight_dimmer),
                    test_mode=       _test_mode,
                    test_pattern=    _test_pattern,
                    strobe_armed=    _strobe_armed,
                    kill_strobe=     _kill_strobe,
                    kill_derby=      _kill_derby,
                    kill_laser=      _kill_laser,
                    flash_active=    _flash_frames > 0,
                    dmx_channels=    _dmx_snapshot,
                )

            # --- frame rate cap (hybrid sleep/spin-lock, ~50µs jitter) ---
            precise_sleep_until(frame_start + _frame_time)

    except KeyboardInterrupt:
        pass
    finally:
        if kbd_thread is not None:
            _stop_keyboard(kbd_thread)
        if midi_in is not None:
            midi_in.close()
        dmx_thread.stop()   # signals thread, sends blackout, closes backend
        capture.stop()
        if overlay:
            overlay.restore_screen()
        print("LightBrain stopped.")


if __name__ == "__main__":
    main()
