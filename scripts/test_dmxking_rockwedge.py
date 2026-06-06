"""
DMXking ultraDMX MAX hardware test — RockWedge fixture.

!! WARNING !!
Only run this script when:
  1. A DMX fixture is physically connected to the DMXking adapter
  2. The fixture is set to the correct DMX address (default: 001)
  3. The fixture is in the correct channel mode (8-channel RGBWAUV)
  4. The fixture is powered on and in a safe location to receive light output

This script walks through a safe test sequence:
  blackout → red → green → blue → white → fade red up/down → blackout

Run from repo root:
  python scripts/test_dmxking_rockwedge.py --port /dev/ttyUSB0
  python scripts/test_dmxking_rockwedge.py --port COM3 --address 1

List available serial ports:
  python scripts/test_dmxking_rockwedge.py --list-ports
"""

import sys
import os
import argparse
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dmx.universe           import DMXUniverse
from dmx.output_enttec_pro  import EnttecProOutput, list_serial_ports
from fixtures.rockwedge     import RockWedge
from engine.gamma           import apply_gamma_to_dmx


DMX_ADDRESS   = 1
HOLD_SECONDS  = 1.5
FADE_STEPS    = 50
FADE_STEP_MS  = 0.04


def send_color(out: EnttecProOutput, universe: DMXUniverse,
               rockwedge: RockWedge,
               r: float, g: float, b: float,
               dimmer: float = 1.0,
               label: str = "") -> None:
    """Write a specific RGB color to the fixture and send one DMX frame."""
    # Convert RGB (0-1) to hue/sat/val for the fixture mapper
    import colorsys
    if r == 0 and g == 0 and b == 0:
        h, s, v = 0.0, 0.0, 0.0
    else:
        h, s, v = colorsys.rgb_to_hsv(r, g, b)

    rockwedge.render_to_universe(
        universe,
        brightness=dimmer,
        hue=h * 360.0,
        saturation=s,
        value=v * dimmer,
    )
    out.send(universe)
    if label:
        print(f"  → {label}")


def main():
    parser = argparse.ArgumentParser(
        description="LightBrain DMXking RockWedge hardware test",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--port",       type=str, default=None,
                        help="serial port (e.g. /dev/ttyUSB0 or COM3)")
    parser.add_argument("--address",    type=int, default=DMX_ADDRESS,
                        help=f"DMX start address (default {DMX_ADDRESS})")
    parser.add_argument("--list-ports", action="store_true",
                        help="list available serial ports and exit")
    args = parser.parse_args()

    if args.list_ports:
        ports = list_serial_ports()
        if not ports:
            print("No serial ports found.")
        else:
            print("\nAvailable serial ports:")
            for p in ports:
                print(f"  {p['port']:<20} {p['description']}")
        sys.exit(0)

    if not args.port:
        print("ERROR: --port is required. Use --list-ports to find available ports.")
        sys.exit(1)

    print("\n" + "=" * 60)
    print("  LIGHTBRAIN — DMXking RockWedge Hardware Test")
    print("=" * 60)
    print(f"  Port:    {args.port}")
    print(f"  Address: {args.address}")
    print()
    print("  !! Ensure fixture is connected, powered, and addressed !!")
    print()
    input("  Press ENTER to begin test sequence, or Ctrl+C to abort: ")

    out = EnttecProOutput()
    try:
        out.open(args.port)
        print(f"  Serial port opened: {args.port}")
    except Exception as e:
        print(f"  ERROR: Could not open {args.port}: {e}")
        sys.exit(1)

    universe  = DMXUniverse()
    rockwedge = RockWedge(
        fixture_id="rw_test",
        name="RockWedge Test",
        dmx_address=args.address,
    )

    try:
        print("\n[1/7] Blackout")
        universe.blackout()
        out.send(universe)
        time.sleep(HOLD_SECONDS)

        print("[2/7] Red full")
        send_color(out, universe, rockwedge, 1.0, 0.0, 0.0, label="Red 255")
        time.sleep(HOLD_SECONDS)

        print("[3/7] Green full")
        send_color(out, universe, rockwedge, 0.0, 1.0, 0.0, label="Green 255")
        time.sleep(HOLD_SECONDS)

        print("[4/7] Blue full")
        send_color(out, universe, rockwedge, 0.0, 0.0, 1.0, label="Blue 255")
        time.sleep(HOLD_SECONDS)

        print("[5/7] White full (RGB)")
        send_color(out, universe, rockwedge, 1.0, 1.0, 1.0, label="White (RGB mix)")
        time.sleep(HOLD_SECONDS)

        print("[6/7] Red fade up then down")
        for step in range(FADE_STEPS + 1):
            level = step / FADE_STEPS
            send_color(out, universe, rockwedge, 1.0, 0.0, 0.0,
                       dimmer=level, label=None)
            time.sleep(FADE_STEP_MS)
        for step in range(FADE_STEPS, -1, -1):
            level = step / FADE_STEPS
            send_color(out, universe, rockwedge, 1.0, 0.0, 0.0,
                       dimmer=level, label=None)
            time.sleep(FADE_STEP_MS)
        print("  → Fade complete")

        print("[7/7] Blackout")
        universe.blackout()
        out.send(universe)
        time.sleep(0.5)

        print("\n  Test sequence complete. Fixture should be dark.")

    except KeyboardInterrupt:
        print("\n  Aborted — sending blackout.")
    finally:
        out.close()  # sends blackout before closing
        print("  Serial port closed.")


if __name__ == "__main__":
    main()
