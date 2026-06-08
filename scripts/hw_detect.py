"""
LightBrain — DMXking / Enttec adapter detection and blackout proof.

Step 1 of hardware validation.  Run this before any fixture test.

Usage:
    python scripts/hw_detect.py --list-ports
    python scripts/hw_detect.py --port /dev/ttyUSB0
    python scripts/hw_detect.py --port COM3

What it does:
    1. Lists serial ports with FTDI VID/PID annotation.
    2. Opens the requested port.
    3. Sends a single universe.blackout() frame.
    4. Asks the operator to confirm the fixture is dark (or power-cycles it).
    5. Logs the result to stdout so it can be captured with tee.

Pass/fail criteria:
    PASS — port opens, blackout frame transmits without error, operator
           confirms any connected fixture is dark.
    FAIL — port not found, open error, serial exception, or operator
           reports fixture is not responding.
"""

import argparse
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dmx.universe          import DMXUniverse
from dmx.output_enttec_pro import EnttecProOutput, list_serial_ports


_KNOWN_FTDI = {
    "0403:6001": "FTDI FT232R (DMXking ultraDMX, Enttec USB Pro, compatible)",
    "0403:6015": "FTDI FT231X",
    "0403:6010": "FTDI FT2232",
    "0403:6011": "FTDI FT4232",
}


def _annotate_ports(ports):
    for p in ports:
        desc = p.get("description", "")
        vid_pid = ""
        for vid_pid_key, label in _KNOWN_FTDI.items():
            vid, pid = vid_pid_key.split(":")
            if vid.upper() in desc.upper() or pid.upper() in desc.upper():
                vid_pid = f"  [{label}]"
                break
        yield p["port"], p["description"], vid_pid


def cmd_list_ports():
    ports = list_serial_ports()
    if not ports:
        print("No serial ports found.")
        return
    print("\nAvailable serial ports:")
    print(f"  {'Port':<22} {'Description':<50} Note")
    print("  " + "-" * 80)
    for port, desc, note in _annotate_ports(ports):
        print(f"  {port:<22} {desc:<50}{note}")
    print()
    dmxking_ports = [
        p for p in ports
        if any(k.split(":")[0].upper() in p.get("description", "").upper()
               for k in _KNOWN_FTDI)
    ]
    if dmxking_ports:
        print(f"  Likely DMX adapter: {dmxking_ports[0]['port']}")
    else:
        print("  No FTDI device detected.  Check USB connection and driver.")


def cmd_detect(port):
    print()
    print("=" * 60)
    print("  LIGHTBRAIN — Adapter Detection + Blackout Proof")
    print("=" * 60)
    print(f"  Port: {port}")
    print()

    # --- Step 1: open port ---
    out = EnttecProOutput()
    try:
        out.open(port)
    except Exception as e:
        print(f"  FAIL  Could not open {port}: {e}")
        print()
        print("  Checklist:")
        print("    [ ] USB cable seated?")
        print("    [ ] Correct port? (run --list-ports)")
        print("    [ ] Linux: user in dialout group?  (sudo usermod -aG dialout $USER)")
        print("    [ ] Linux: latency timer set to 1?  (cat /sys/class/tty/ttyUSB0/device/latency_timer)")
        print("    [ ] Windows: correct COM port? Device Manager → Ports")
        sys.exit(1)

    print(f"  PASS  Serial port opened: {port}")

    # --- Step 2: send blackout frame ---
    universe = DMXUniverse()
    try:
        universe.blackout()
        out.send(universe)
        time.sleep(0.1)
        # Send three times to ensure the frame lands
        out.send(universe)
        time.sleep(0.1)
        out.send(universe)
    except Exception as e:
        print(f"  FAIL  Could not send DMX frame: {e}")
        out.close()
        sys.exit(1)

    print("  PASS  Blackout frame transmitted (3 frames, 100ms apart)")
    print()
    print("  If a fixture is connected and powered:")
    print("    → It should now be DARK (all channels zero).")
    print("    → If it was already dark, power-cycle it and observe it")
    print("      return to dark immediately after startup.")
    print()

    confirm = input("  Confirm fixture is dark? [y/n/skip]: ").strip().lower()
    if confirm == "y":
        print("  PASS  Operator confirmed blackout on hardware")
        result = "PASS"
    elif confirm == "skip":
        print("  SKIP  No fixture connected — blackout frame sent without hardware confirmation")
        result = "SKIP"
    else:
        print("  FAIL  Operator reported fixture did NOT respond to blackout")
        print("        Check: DMX cable, fixture address, fixture power, DMX IN port")
        result = "FAIL"

    out.close()
    print()
    print(f"  Detection result: {result}")
    print(f"  Port: {port}")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="LightBrain adapter detection + blackout proof",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--port",       type=str, default=None,
                        help="serial port (e.g. /dev/ttyUSB0 or COM3)")
    parser.add_argument("--list-ports", action="store_true",
                        help="list available serial ports and exit")
    args = parser.parse_args()

    if args.list_ports:
        cmd_list_ports()
    elif args.port:
        cmd_detect(args.port)
    else:
        parser.print_help()
        print("\nRun with --list-ports to find your adapter, then --port <PORT> to test it.")
        sys.exit(1)


if __name__ == "__main__":
    main()
