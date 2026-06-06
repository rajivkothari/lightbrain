"""
LightBrain Sprint 1 — main application entry point.

Pipeline:
  audio input → FFT analysis → EMA smoothing → room lane →
  RockWedge mapper → DMX universe → mock output → terminal overlay

Run from the repo root:
  python -m app.main [--device N] [--palette NAME] [--mode NAME]

Keyboard shortcuts (non-blocking, best-effort in terminal):
  O = Open Dance  D = Dinner  B = Banger
  I = Indian/Latin  S = Speech  L = Slow Dance
  Space = Blackout toggle  Q = Quit

TODO: keyboard input is currently config-variable only — implement
      non-blocking stdin or curses input in Sprint 2.
"""

import argparse
import json
import os
import sys
import time
import colorsys
import threading

# ---- path setup (run from repo root) ----
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from audio.input    import AudioCapture
from audio.analyzer import AudioAnalyzer, AudioBands

from engine.smoothing import LaneSmoother
from engine.palettes  import load_all_palettes, PaletteBlender
from engine.lanes     import RoomLane
from engine.modes     import get_mode, MODES, KEYBOARD_MAP
from engine.safety    import SafetyEngine

from dmx.universe        import DMXUniverse
from dmx.output_mock     import MockDMXOutput
from dmx.output_enttec_pro import EnttecProOutput

from fixtures.rockwedge import RockWedge

from ui.terminal_debug import TerminalDebugOverlay


# ---- constants ----
CONFIG_PATH   = os.path.join(ROOT, "config", "rig_config.json")
PALETTES_DIR  = os.path.join(ROOT, "config", "palettes")
TARGET_FPS    = 40
FRAME_TIME    = 1.0 / TARGET_FPS


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return json.load(f)


def main():
    parser = argparse.ArgumentParser(description="LightBrain Sprint 1")
    parser.add_argument("--device",  type=int,  default=None,
                        help="sounddevice input device index")
    parser.add_argument("--palette", type=str,  default=None,
                        help="starting palette key (e.g. open_dance)")
    parser.add_argument("--mode",    type=str,  default=None,
                        help="starting mode key (e.g. dinner)")
    parser.add_argument("--serial",  type=str,  default=None,
                        help="serial port for DMXking (e.g. /dev/ttyUSB0)")
    parser.add_argument("--verbose-dmx", action="store_true",
                        help="print every changed DMX channel to console")
    args = parser.parse_args()

    config = load_config()

    # ---- audio setup ----
    device_index = args.device if args.device is not None \
                   else config["audio"].get("device_index")
    sample_rate  = config["audio"]["sample_rate"]
    block_size   = config["audio"]["block_size"]

    capture  = AudioCapture(device_index, sample_rate, block_size)
    analyzer = AudioAnalyzer(sample_rate, block_size)

    # ---- palettes ----
    all_palettes = load_all_palettes(PALETTES_DIR)
    if not all_palettes:
        print("[ERROR] No palettes found in config/palettes/")
        sys.exit(1)

    # ---- mode / palette init ----
    mode_key    = args.mode    or config.get("default_mode", "open_dance")
    palette_key = args.palette or config.get("default_palette", "open_dance")

    current_mode    = get_mode(mode_key)
    current_palette = all_palettes.get(palette_key,
                                        list(all_palettes.values())[0])

    # ---- engine ----
    smoother = LaneSmoother()
    safety   = SafetyEngine()
    safety.update_from_mode(current_mode)

    room_lane = RoomLane(current_palette)

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

    # ---- start audio capture ----
    error_msg = None
    try:
        capture.start()
    except Exception as e:
        error_msg = f"Audio start failed: {e}"

    last_bands  = AudioBands()
    last_lanes  = {"impact": 0.0, "room": 0.0}
    frame_start = time.monotonic()

    try:
        while True:
            frame_start = time.monotonic()

            # --- audio ---
            block = capture.get_latest_block()
            if block is not None:
                last_bands = analyzer.analyze(block)
            else:
                # Silence: decay toward zero
                last_bands = AudioBands(0.0, 0.0, 0.0, 0.0)

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

            # --- channel values for overlay ---
            ch_labels = rockwedge.get_channel_labels()
            dmx_ch_vals = {
                label: universe.get_channel(ch)
                for label, ch in ch_labels.items()
            }

            # --- overlay render ---
            overlay.update(
                device_name=capture.device_name or "no device",
                raw_bands=bands_dict,
                smoothed_lanes=last_lanes,
                mode_name=current_mode.display_name,
                palette_name=room_lane.palette_name,
                hsv=(room_out.hsv.h, room_out.hsv.s, room_out.hsv.v),
                rgb=rgb_out,
                fixture_name=rockwedge.name,
                dmx_address=rockwedge.dmx_address,
                dmx_channels=dmx_ch_vals,
                dmx_output_type=dmx_out.output_type,
                safety_blackout=safety.state.blackout_active,
                safety_strobe_ok=safety.state.strobe_allowed,
                error=error_msg or capture.last_error,
            )

            # --- frame rate cap ---
            elapsed = time.monotonic() - frame_start
            sleep_t = FRAME_TIME - elapsed
            if sleep_t > 0:
                time.sleep(sleep_t)

    except KeyboardInterrupt:
        pass
    finally:
        universe.blackout()
        dmx_out.send(universe)
        dmx_out.close()
        capture.stop()
        overlay.restore_screen()
        print("LightBrain stopped.")


if __name__ == "__main__":
    main()
