"""
LightBrain Sprint 1B — main application entry point.

Pipeline:
  audio input → FFT analysis → EMA smoothing → room lane →
  RockWedge mapper → DMX universe → mock output → terminal overlay

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

from audio.analyzer  import AudioAnalyzer, AudioBands
from audio.synthetic import SyntheticAudioSource

from engine.smoothing import LaneSmoother
from engine.palettes  import load_all_palettes, PaletteBlender
from engine.lanes     import RoomLane
from engine.modes     import get_mode, MODES, KEYBOARD_MAP
from engine.safety    import SafetyEngine

from dmx.universe          import DMXUniverse
from dmx.output_mock       import MockDMXOutput
from dmx.output_enttec_pro import EnttecProOutput

from fixtures.rockwedge import RockWedge

from ui.terminal_debug import TerminalDebugOverlay


# ---- constants ----
CONFIG_PATH  = os.path.join(ROOT, "config", "rig_config.json")
PALETTES_DIR = os.path.join(ROOT, "config", "palettes")
TARGET_FPS   = 40
FRAME_TIME   = 1.0 / TARGET_FPS


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
    parser = argparse.ArgumentParser(description="LightBrain Sprint 1B")
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

    analyzer = AudioAnalyzer()

    # ---- engine ----
    smoother = LaneSmoother()
    safety   = SafetyEngine()
    safety.update_from_mode(current_mode)
    room_lane = RoomLane(current_palette, mode=current_mode)

    # ---- fixture ----
    fixture_cfg = config["fixtures"][0]
    rockwedge   = RockWedge(
        fixture_id=fixture_cfg["id"],
        name=fixture_cfg["name"],
        dmx_address=fixture_cfg["dmx_address"],
        lane=fixture_cfg["lane"],
        group=fixture_cfg["group"],
    )

    # ---- DMX output ----
    universe = DMXUniverse()

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

    # ---- UI ----
    overlay = TerminalDebugOverlay()
    overlay.init_screen()

    # ---- keyboard thread ----
    kbd_thread = _start_keyboard()

    last_bands = AudioBands()
    last_lanes = {"impact": 0.0, "room": 0.0}
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
                elif key in KEYBOARD_MAP:
                    mode_key = KEYBOARD_MAP[key]
                    if mode_key == "quit":
                        quit_flag = True
                        break
                    elif mode_key == "blackout":
                        safety.toggle_blackout()
                    else:
                        current_mode    = get_mode(mode_key)
                        current_palette = all_palettes.get(
                            current_mode.palette_key, current_palette
                        )
                        safety.update_from_mode(current_mode)
                        room_lane.set_mode(current_mode)
                        room_lane.set_palette(current_palette)

            if quit_flag:
                break

            # --- audio ---
            block = capture.get_latest_block()
            if block is not None:
                last_bands = analyzer.analyze(block)
            else:
                last_bands = AudioBands()

            bands_dict = last_bands.as_dict()

            # --- smoothing ---
            last_lanes = smoother.update(bands_dict)

            # --- lane render ---
            room_out = room_lane.render(
                smoothed_room=last_lanes["room"],
                impact=last_lanes["impact"],
                safety=safety,
                master_dimmer=1.0,
                group_intensity=1.0,
            )

            # --- fixture write ---
            rockwedge.render_to_universe(
                universe,
                brightness=1.0,
                hue=room_out.hsv.h,
                saturation=room_out.hsv.s,
                value=room_out.hsv.v,
                strobe=room_out.strobe,
            )

            # --- DMX send ---
            dmx_out.send(universe)

            # --- build RGB for overlay ---
            h_norm = room_out.hsv.h / 360.0
            r_f, g_f, b_f = colorsys.hsv_to_rgb(
                h_norm, room_out.hsv.s, room_out.hsv.v
            )
            rgb_out = (int(r_f * 255), int(g_f * 255), int(b_f * 255))

            ch_labels   = rockwedge.get_channel_labels()
            dmx_ch_vals = {
                label: universe.get_channel(ch)
                for label, ch in ch_labels.items()
            }

            # --- overlay render ---
            overlay.update(
                device_name=device_label,
                raw_bands=bands_dict,
                smoothed_lanes=last_lanes,
                mode_name=current_mode.display_name,
                palette_name=room_lane.palette_name,
                color_name=room_lane.current_color_name,
                next_color_name=room_lane.next_color_name,
                hold_remaining_ms=room_lane.hold_remaining_ms,
                transition_progress=room_lane.transition_progress,
                hsv=(room_out.hsv.h, room_out.hsv.s, room_out.hsv.v),
                rgb=rgb_out,
                brightness_base=room_out.base_brightness,
                brightness_pulse=room_out.pulse_brightness,
                fixture_name=rockwedge.name,
                dmx_address=rockwedge.dmx_address,
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
        universe.blackout()
        dmx_out.send(universe)
        dmx_out.close()
        capture.stop()
        overlay.restore_screen()
        print("LightBrain stopped.")


if __name__ == "__main__":
    main()
