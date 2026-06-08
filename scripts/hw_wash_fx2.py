"""
LightBrain — Chauvet Wash FX2 hardware validation script.

Tests the fixture mapper against real hardware using the 8Ch personality.

Run AFTER hw_detect.py confirms the adapter is working.

Usage:
    python scripts/hw_wash_fx2.py --port /dev/ttyUSB0 --address 1
    python scripts/hw_wash_fx2.py --port COM3 --address 9   # for right unit

Log to file:
    python scripts/hw_wash_fx2.py --port /dev/ttyUSB0 --address 1 2>&1 | tee washfx2_addr1.log

Expected channel map (8Ch personality):
    Ch 1  Red
    Ch 2  Green
    Ch 3  Blue
    Ch 4  UV
    Ch 5  Strobe  (0=off, 1–255 = 0.5–20 Hz)
    Ch 6  Auto Program  (keep at 0 for manual colour)
    Ch 7  Auto Program Speed  (unused in manual mode)
    Ch 8  Dimmer  (only active in auto mode — expect no response in manual mode)

In 8Ch manual mode brightness is controlled by scaling RGB/UV values directly.
There is NO separate master dimmer for manual colour output.

Test sequence:
    1. Blackout (all zeros)
    2. Red full
    3. Green full
    4. Blue full
    5. UV full (fixture-dependent glow — may appear blue-purple)
    6. White (RGB mix: R+G+B full)
    7. Warm white (R+G heavy, B light — test amber/warm rendering)
    8. Strobe at mid speed (Ch5 = 128)
    9. Strobe off + blackout
    10. Dimmer ramp (red, fade 0→1→0 over ~2s each direction)
    11. Final blackout

Pass/fail for each step recorded to stdout.
"""

import argparse
import sys
import os
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dmx.universe             import DMXUniverse
from dmx.output_enttec_pro    import EnttecProOutput
from fixtures.chauvet_wash_fx2 import ChauvetWashFX2


HOLD_S      = 2.0
FADE_STEPS  = 60
FADE_STEP_S = 1.0 / FADE_STEPS   # ~1s per direction


def _log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def _confirm(prompt):
    """Prompt operator and return 'pass', 'fail', or 'skip'."""
    ans = input(f"         {prompt} [p=pass / f=fail / s=skip]: ").strip().lower()
    if ans.startswith("p"):
        return "pass"
    if ans.startswith("f"):
        return "fail"
    return "skip"


def _send(out, universe, fixture, brightness, hue, saturation, value,
          strobe=0.0, white=0.0, uv=0.0, label=""):
    fixture.render_to_universe(
        universe,
        brightness=brightness,
        hue=hue,
        saturation=saturation,
        value=value,
        strobe=strobe,
        white=white,
        uv=uv,
    )
    out.send(universe)
    if label:
        # Show DMX values for the fixture address range
        base = fixture.dmx_address - 1
        vals = [universe.get_channel(fixture.dmx_address + i) for i in range(fixture.channel_count)]
        _log(f"{label:<40} DMX ch{fixture.dmx_address}-{fixture.dmx_address+fixture.channel_count-1}: {vals}")


def _step(out, universe, fixture, label, brightness, hue, sat, val,
          strobe=0.0, white=0.0, uv=0.0, expect=""):
    _send(out, universe, fixture, brightness, hue, sat, val,
          strobe=strobe, white=white, uv=uv, label=label)
    if expect:
        print(f"         Expected: {expect}")
    result = _confirm("What did the fixture do?")
    _log(f"  RESULT  {label}: {result.upper()}")
    return result


def main():
    parser = argparse.ArgumentParser(
        description="Chauvet Wash FX2 hardware validation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--port",    required=True, type=str,
                        help="serial port (e.g. /dev/ttyUSB0 or COM3)")
    parser.add_argument("--address", type=int, default=1,
                        help="fixture DMX start address (default 1)")
    args = parser.parse_args()

    print()
    print("=" * 64)
    print("  LIGHTBRAIN — Chauvet Wash FX2 Hardware Validation")
    print("=" * 64)
    print(f"  Port      : {args.port}")
    print(f"  Address   : {args.address}  (channels {args.address}–{args.address + 7})")
    print(f"  Personality: 8Ch (must be set on the fixture)")
    print()
    print("  !! Confirm the fixture is set to 8Ch personality before proceeding !!")
    print()
    input("  Press ENTER to begin, or Ctrl+C to abort: ")
    print()

    out = EnttecProOutput()
    try:
        out.open(args.port)
        _log(f"Serial port opened: {args.port}")
    except Exception as e:
        print(f"  FAIL  Could not open {args.port}: {e}")
        sys.exit(1)

    universe = DMXUniverse()
    fixture  = ChauvetWashFX2(
        fixture_id="washfx2_val",
        name="Wash FX2 Validation",
        dmx_address=args.address,
    )

    results = {}
    try:
        # ── Step 1: Blackout ──────────────────────────────────────────────
        print("[1/10] Blackout — all 512 channels zero")
        universe.blackout()
        out.send(universe)
        time.sleep(0.3)
        out.send(universe)
        print(f"         DMX ch {args.address}–{args.address+7}: all 0")
        result = _confirm("Fixture completely dark?")
        results["blackout"] = result
        _log(f"  RESULT  blackout: {result.upper()}")
        time.sleep(0.5)

        # ── Step 2: Red ───────────────────────────────────────────────────
        print("\n[2/10] Red full (Ch1=255, Ch2=0, Ch3=0, Ch4=0)")
        result = _step(out, universe, fixture, "red_full",
                       brightness=1.0, hue=0.0, sat=1.0, val=1.0,
                       expect="All zones solid red")
        results["red"] = result
        time.sleep(HOLD_S)

        # ── Step 3: Green ─────────────────────────────────────────────────
        print("\n[3/10] Green full (Ch1=0, Ch2=255, Ch3=0)")
        result = _step(out, universe, fixture, "green_full",
                       brightness=1.0, hue=120.0, sat=1.0, val=1.0,
                       expect="All zones solid green")
        results["green"] = result
        time.sleep(HOLD_S)

        # ── Step 4: Blue ──────────────────────────────────────────────────
        print("\n[4/10] Blue full (Ch1=0, Ch2=0, Ch3=255)")
        result = _step(out, universe, fixture, "blue_full",
                       brightness=1.0, hue=240.0, sat=1.0, val=1.0,
                       expect="All zones solid blue")
        results["blue"] = result
        time.sleep(HOLD_S)

        # ── Step 5: UV ────────────────────────────────────────────────────
        print("\n[5/10] UV full (Ch4=255, Ch1/2/3=0)")
        result = _step(out, universe, fixture, "uv_full",
                       brightness=1.0, hue=0.0, sat=0.0, val=0.0, uv=1.0,
                       expect="UV LEDs lit (blue-purple glow, may be subtle)")
        results["uv"] = result
        time.sleep(HOLD_S)

        # ── Step 6: White (RGB mix) ───────────────────────────────────────
        print("\n[6/10] White RGB mix (Ch1=Ch2=Ch3=255)")
        result = _step(out, universe, fixture, "white_rgb",
                       brightness=1.0, hue=0.0, sat=0.0, val=1.0,
                       expect="All zones bright white (RGB mix)")
        results["white"] = result
        time.sleep(HOLD_S)

        # ── Step 7: Warm white ────────────────────────────────────────────
        print("\n[7/10] Warm white (R heavy, G mid, B low)")
        result = _step(out, universe, fixture, "warm_white",
                       brightness=1.0, hue=30.0, sat=0.35, val=1.0,
                       expect="Warm amber-white (should look warmer than step 6)")
        results["warm_white"] = result
        time.sleep(HOLD_S)

        # ── Step 8: Strobe ────────────────────────────────────────────────
        print("\n[8/10] Strobe at mid speed (Ch5≈128, white output)")
        print("         !! Strobe will fire — warn anyone in the room !!")
        ans = input("         Proceed with strobe test? [y/skip]: ").strip().lower()
        if ans == "y":
            result = _step(out, universe, fixture, "strobe_mid",
                           brightness=1.0, hue=0.0, sat=0.0, val=1.0,
                           strobe=0.5,
                           expect="Fixture strobing at ~10 Hz white")
            results["strobe"] = result
            time.sleep(HOLD_S)
        else:
            _log("  RESULT  strobe_mid: SKIPPED")
            results["strobe"] = "skip"

        # ── Step 9: Strobe off + blackout ─────────────────────────────────
        print("\n[9/10] Strobe off + blackout")
        universe.blackout()
        out.send(universe)
        time.sleep(0.3)
        out.send(universe)
        print("         Expected: fixture goes dark / stops strobing")
        result = _confirm("Fixture dark after strobe off?")
        results["strobe_off"] = result
        _log(f"  RESULT  strobe_off: {result.upper()}")
        time.sleep(0.5)

        # ── Step 10: Dimmer ramp ──────────────────────────────────────────
        print("\n[10/10] Dimmer ramp — red, 0→full→0 over ~2s each direction")
        print("         Expected: smooth fade up then smooth fade down")
        for step in range(FADE_STEPS + 1):
            brt = step / FADE_STEPS
            _send(out, universe, fixture, brt, 0.0, 1.0, 1.0)
            time.sleep(FADE_STEP_S)
        for step in range(FADE_STEPS, -1, -1):
            brt = step / FADE_STEPS
            _send(out, universe, fixture, brt, 0.0, 1.0, 1.0)
            time.sleep(FADE_STEP_S)
        result = _confirm("Was the fade smooth with no flicker or stepped jumps?")
        results["dimmer_ramp"] = result
        _log(f"  RESULT  dimmer_ramp: {result.upper()}")

    except KeyboardInterrupt:
        _log("Aborted by operator")
    finally:
        universe.blackout()
        try:
            out.send(universe)
            time.sleep(0.1)
            out.send(universe)
        except Exception:
            pass
        out.close()
        _log("Serial port closed — fixture dark")

    # ── Summary ───────────────────────────────────────────────────────────
    print()
    print("=" * 64)
    print("  VALIDATION SUMMARY — Wash FX2")
    print(f"  Port {args.port}  ·  Address {args.address}")
    print("=" * 64)
    fails  = [k for k, v in results.items() if v == "fail"]
    skips  = [k for k, v in results.items() if v == "skip"]
    passes = [k for k, v in results.items() if v == "pass"]
    for step, res in results.items():
        marker = {"pass": "PASS", "fail": "FAIL", "skip": "SKIP"}.get(res, "?")
        print(f"  {marker}  {step}")
    print()
    if fails:
        print(f"  FAILURES: {', '.join(fails)}")
        print("  → Document failures in docs/HARDWARE_VALIDATION_LOG.md")
    elif skips:
        print(f"  Passed with {len(skips)} step(s) skipped: {', '.join(skips)}")
    else:
        print("  All steps passed.")
    print()


if __name__ == "__main__":
    main()
