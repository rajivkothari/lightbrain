"""
LightBrain — Raw DMX channel sweep for fixture channel-map verification.

Run this BEFORE the fixture-specific tests to verify that the fixture
responds on the expected channels and to detect any personality mismatch.

The sweep sets one channel at a time to a test value (default 128) while
all others remain at 0, then waits for the operator to log what the fixture
actually does.  This is the ground-truth check against the documented
channel map.

Usage:
    # Sweep all 8 channels of Wash FX2 at address 1
    python scripts/hw_channel_sweep.py --port /dev/ttyUSB0 --address 1 --channels 8

    # Sweep all 29 channels of GigBAR at address 17
    python scripts/hw_channel_sweep.py --port /dev/ttyUSB0 --address 17 --channels 29

    # Full-value sweep (255) instead of half (128)
    python scripts/hw_channel_sweep.py --port /dev/ttyUSB0 --address 1 --channels 8 --value 255

    # Fast mode — 2s per channel instead of waiting for keypress
    python scripts/hw_channel_sweep.py --port /dev/ttyUSB0 --address 1 --channels 8 --auto 2

Logs results to stdout; redirect to a file with:
    python scripts/hw_channel_sweep.py ... 2>&1 | tee sweep_washfx2_addr1.log

Expected channel maps (for reference during sweep):

Wash FX2 (8Ch personality):
  Ch 1  Red
  Ch 2  Green
  Ch 3  Blue
  Ch 4  UV
  Ch 5  Strobe
  Ch 6  Auto Program (should stay 0 — manual mode)
  Ch 7  Auto Program Speed (unused in manual mode)
  Ch 8  Dimmer (only active in auto mode — expect no response in manual mode)

GigBAR Move+ILS (29Ch personality):
  Ch  1  Par Red
  Ch  2  Par Green
  Ch  3  Par Blue
  Ch  4  Par Amber
  Ch  5  Par White
  Ch  6  Par UV
  Ch  7  Par Strobe
  Ch  8  Derby Red
  Ch  9  Derby Green
  Ch 10  Derby Blue
  Ch 11  Derby White
  Ch 12  Derby Strobe
  Ch 13  Derby Rotation
  Ch 14  Flash LED 1
  Ch 15  Flash LED 2
  Ch 16  Flash LED 3
  Ch 17  Flash LED 4
  Ch 18  Flash Strobe
  Ch 19  Laser control  !! laser may activate — warn operator before this channel
  Ch 20  Laser patterns
  Ch 21  Spot Pan
  Ch 22  Spot Fine Pan
  Ch 23  Spot Tilt
  Ch 24  Spot Fine Tilt
  Ch 25  Spot Pan/Tilt Speed
  Ch 26  Spot Color Wheel
  Ch 27  Spot Gobo Wheel
  Ch 28  Spot Dimmer
  Ch 29  Spot Strobe
"""

import argparse
import sys
import os
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dmx.universe          import DMXUniverse
from dmx.output_enttec_pro import EnttecProOutput


# Channels where the operator should be warned before sending a value.
# Laser activation during validation must be intentional.
_WARN_CHANNELS_BY_BASE = {
    # GigBAR: offset 18 = Ch 19 laser control
    18: "!! LASER control — value 128 may activate laser patterns. Confirm area is clear.",
    19: "!! LASER patterns",
}


def _log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


def main():
    parser = argparse.ArgumentParser(
        description="LightBrain raw DMX channel sweep for channel-map verification",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--port",     required=True, type=str,
                        help="serial port (e.g. /dev/ttyUSB0 or COM3)")
    parser.add_argument("--address",  required=True, type=int,
                        help="fixture DMX start address (1-indexed)")
    parser.add_argument("--channels", required=True, type=int,
                        help="number of channels to sweep")
    parser.add_argument("--value",    type=int, default=128,
                        help="DMX value to send to each test channel (default 128)")
    parser.add_argument("--auto",     type=float, default=0,
                        help="auto-advance after N seconds (default 0 = wait for keypress)")
    args = parser.parse_args()

    if args.value < 1 or args.value > 255:
        print("ERROR: --value must be 1–255")
        sys.exit(1)
    if args.address < 1 or args.address + args.channels - 1 > 512:
        print(f"ERROR: address {args.address} + {args.channels} channels exceeds DMX universe (512)")
        sys.exit(1)

    print()
    print("=" * 64)
    print("  LIGHTBRAIN — Raw DMX Channel Sweep")
    print("=" * 64)
    print(f"  Port     : {args.port}")
    print(f"  Address  : {args.address}")
    print(f"  Channels : {args.channels}  (Ch {args.address} – Ch {args.address + args.channels - 1})")
    print(f"  Test val : {args.value}  (0x{args.value:02X})")
    print(f"  Advance  : {'auto ' + str(args.auto) + 's' if args.auto else 'keypress'}")
    print()
    print("  For each channel the sweep sends:")
    print("    ALL other channels → 0")
    print("    TEST channel → " + str(args.value))
    print()
    print("  Record what the fixture actually does for each channel.")
    print("  Compare against the expected map listed in the script header.")
    print()
    input("  Press ENTER to begin sweep, or Ctrl+C to abort: ")

    out = EnttecProOutput()
    try:
        out.open(args.port)
        _log(f"Serial port opened: {args.port}")
    except Exception as e:
        print(f"  FAIL  Could not open {args.port}: {e}")
        sys.exit(1)

    universe = DMXUniverse()

    try:
        # Send blackout first
        universe.blackout()
        out.send(universe)
        time.sleep(0.5)
        _log("Blackout sent — fixture should be dark")
        print()

        for offset in range(args.channels):
            ch_number = args.address + offset   # 1-indexed DMX channel
            ch_offset = offset                   # 0-indexed offset within fixture

            # Warn before potentially dangerous channels
            warn = _WARN_CHANNELS_BY_BASE.get(ch_offset)
            if warn:
                print(f"  !! WARNING: {warn}")
                ans = input(f"     Send value {args.value} to Ch {ch_number}? [y/skip]: ").strip().lower()
                if ans != "y":
                    _log(f"Ch {ch_number:3d} (offset {offset+1:2d}): SKIPPED by operator")
                    continue

            # Build frame: everything zero except this channel
            universe.blackout()
            universe.set_channel(ch_number, args.value)
            out.send(universe)

            if args.auto:
                _log(f"Ch {ch_number:3d} (offset {offset+1:2d}): value {args.value}  — auto {args.auto}s")
                time.sleep(args.auto)
                observation = "(auto-advance)"
            else:
                obs_input = input(
                    f"  Ch {ch_number:3d} (offset {offset+1:2d}): value {args.value}  "
                    f"— what do you see? (Enter to skip): "
                ).strip()
                observation = obs_input if obs_input else "(no note)"
            _log(f"Ch {ch_number:3d} (offset {offset+1:2d}): val={args.value}  observed={observation!r}")

        # Final blackout
        universe.blackout()
        out.send(universe)
        time.sleep(0.2)
        out.send(universe)
        _log("Sweep complete — blackout sent")

    except KeyboardInterrupt:
        _log("Aborted by operator — sending blackout")
    finally:
        universe.blackout()
        try:
            out.send(universe)
        except Exception:
            pass
        out.close()
        _log("Serial port closed")


if __name__ == "__main__":
    main()
