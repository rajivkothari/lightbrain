"""
Full mock pipeline test: audio → analysis → smoothing → room lane →
RockWedge mapper → mock DMX → terminal overlay.

No physical DMX hardware required.

Run from repo root:
  python scripts/test_mock_rockwedge.py                         # real mic, open_dance
  python scripts/test_mock_rockwedge.py --demo                  # synthetic audio
  python scripts/test_mock_rockwedge.py --device 2 --palette banger
  python scripts/test_mock_rockwedge.py --demo --mode dinner

Press Ctrl+C to stop.
"""

import sys
import os
import argparse
import time
import colorsys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audio.analyzer  import AudioAnalyzer, AudioBands
from audio.synthetic import SyntheticAudioSource

from engine.smoothing import LaneSmoother
from engine.palettes  import load_all_palettes
from engine.lanes     import RoomLane
from engine.modes     import get_mode
from engine.safety    import SafetyEngine

from dmx.universe    import DMXUniverse
from dmx.output_mock import MockDMXOutput

from fixtures.rockwedge import RockWedge

from ui.terminal_debug import TerminalDebugOverlay


ROOT         = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PALETTES_DIR = os.path.join(ROOT, "config", "palettes")
TARGET_FPS   = 40
FRAME_TIME   = 1.0 / TARGET_FPS


def main():
    parser = argparse.ArgumentParser(description="LightBrain mock RockWedge test")
    parser.add_argument("--device",  type=int, default=None,
                        help="sounddevice input device index")
    parser.add_argument("--palette", type=str, default="open_dance",
                        help="starting palette key (default: open_dance)")
    parser.add_argument("--mode",    type=str, default="open_dance",
                        help="starting mode key (default: open_dance)")
    parser.add_argument("--demo",    action="store_true",
                        help="use synthetic audio — no microphone needed")
    args = parser.parse_args()

    # ---- palettes ----
    all_palettes = load_all_palettes(PALETTES_DIR)
    if not all_palettes:
        print("[ERROR] No palettes found. Run from the repo root directory.")
        sys.exit(1)

    palette_key = args.palette if args.palette in all_palettes else "open_dance"
    palette     = all_palettes.get(palette_key, list(all_palettes.values())[0])
    mode        = get_mode(args.mode)

    # ---- audio source ----
    error_msg = None

    if args.demo:
        capture = SyntheticAudioSource(block_size=1024)
        capture.start()
        device_label = capture.device_name
    else:
        from audio.input import AudioCapture
        capture = AudioCapture(device_index=args.device, block_size=1024)
        try:
            capture.start()
            device_label = capture.device_name or "unknown device"
        except Exception as e:
            error_msg    = f"Audio open failed: {e} — run with --demo"
            device_label = "no device (use --demo)"

    # ---- engine ----
    analyzer  = AudioAnalyzer()
    smoother  = LaneSmoother()
    safety    = SafetyEngine()
    safety.update_from_mode(mode)
    room_lane = RoomLane(palette)

    # ---- fixture + DMX ----
    rockwedge = RockWedge(
        fixture_id="rw_001",
        name="RockWedge LED",
        dmx_address=1,
    )
    universe = DMXUniverse()
    dmx_out  = MockDMXOutput(verbose=False)
    overlay  = TerminalDebugOverlay()
    overlay.init_screen()

    try:
        while True:
            t0 = time.monotonic()

            # ---- audio ----
            block = capture.get_latest_block()
            if block is not None:
                bands = analyzer.analyze(block)
            else:
                bands = AudioBands()

            # ---- smoothing ----
            lanes = smoother.update(bands.as_dict())

            # ---- room lane ----
            room_out = room_lane.render(
                smoothed_room=lanes["room"],
                impact=lanes["impact"],
                safety=safety,
            )

            # ---- fixture write ----
            rockwedge.render_to_universe(
                universe,
                brightness=1.0,
                hue=room_out.hsv.h,
                saturation=room_out.hsv.s,
                value=room_out.hsv.v,
                strobe=room_out.strobe,
            )

            # ---- DMX send ----
            dmx_out.send(universe)

            # ---- build RGB for overlay ----
            r_f, g_f, b_f = colorsys.hsv_to_rgb(
                room_out.hsv.h / 360.0,
                room_out.hsv.s,
                room_out.hsv.v,
            )
            rgb_out = (int(r_f * 255), int(g_f * 255), int(b_f * 255))

            ch_vals = {
                label: universe.get_channel(ch)
                for label, ch in rockwedge.get_channel_labels().items()
            }

            # ---- render overlay ----
            overlay.update(
                device_name=device_label,
                raw_bands=bands.as_dict(),
                smoothed_lanes=lanes,
                mode_name=mode.display_name,
                palette_name=room_lane.palette_name,
                hsv=(room_out.hsv.h, room_out.hsv.s, room_out.hsv.v),
                rgb=rgb_out,
                fixture_name=rockwedge.name,
                dmx_address=rockwedge.dmx_address,
                dmx_channels=ch_vals,
                dmx_output_type="MOCK",
                safety_blackout=safety.state.blackout_active,
                safety_strobe_ok=safety.state.strobe_allowed,
                error=error_msg or getattr(capture, "last_error", None),
            )

            # ---- frame rate cap ----
            elapsed = time.monotonic() - t0
            remaining = FRAME_TIME - elapsed
            if remaining > 0:
                time.sleep(remaining)

    except KeyboardInterrupt:
        pass
    finally:
        universe.blackout()
        dmx_out.send(universe)
        capture.stop()
        overlay.restore_screen()
        print("Mock test stopped.")


if __name__ == "__main__":
    main()
