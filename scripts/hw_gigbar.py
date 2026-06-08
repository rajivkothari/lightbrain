"""
LightBrain — Chauvet GigBAR Move+ILS hardware validation script.

Tests all five sections of the fixture mapper: Par wash, Derby, Flash LEDs,
Laser, and Spot head.  Run after hw_detect.py and hw_wash_fx2.py.

Usage:
    python scripts/hw_gigbar.py --port /dev/ttyUSB0 --address 17

Log to file:
    python scripts/hw_gigbar.py --port /dev/ttyUSB0 --address 17 2>&1 | tee gigbar_addr17.log

Expected 29Ch personality layout:
  Ch  1  Par Red          Ch  8  Derby Red         Ch 14–17  Flash LEDs
  Ch  2  Par Green        Ch  9  Derby Green        Ch 18     Flash Strobe
  Ch  3  Par Blue         Ch 10  Derby Blue         Ch 19     Laser control
  Ch  4  Par Amber        Ch 11  Derby White        Ch 20     Laser patterns
  Ch  5  Par White        Ch 12  Derby Strobe       Ch 21–24  Spot Pan/Tilt
  Ch  6  Par UV           Ch 13  Derby Rotation     Ch 25     Pan/Tilt Speed
  Ch  7  Par Strobe                                 Ch 26–29  Spot Color/Gobo/Dim/Strobe

Safety notes:
  - Laser test step is opt-in: you must type "YES LASER" to enable it.
  - Strobe tests prompt before firing.
  - Derby rotation test is visible — warn anyone nearby.
  - Spot head will MOVE during pan/tilt test — clear a 1m radius.
"""

import argparse
import sys
import os
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dmx.universe                      import DMXUniverse
from dmx.output_enttec_pro             import EnttecProOutput
from fixtures.chauvet_gigbar_move_ils  import ChauvetGigBarMoveILS


HOLD_S      = 2.0
FADE_STEPS  = 60
FADE_STEP_S = 1.0 / FADE_STEPS


def _log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def _confirm(prompt):
    ans = input(f"         {prompt} [p=pass / f=fail / s=skip]: ").strip().lower()
    if ans.startswith("p"):
        return "pass"
    if ans.startswith("f"):
        return "fail"
    return "skip"


def _dmx_snapshot(universe, address, count):
    return [universe.get_channel(address + i) for i in range(count)]


def _send_and_log(out, universe, fixture, label=""):
    out.send(universe)
    if label:
        vals = _dmx_snapshot(universe, fixture.dmx_address, fixture.channel_count)
        _log(f"{label:<50} ch{fixture.dmx_address}-{fixture.dmx_address+fixture.channel_count-1}: {vals}")


def _step_render(out, universe, fixture, label, brightness, hue, sat, val,
                 strobe=0.0, white=0.0, amber=0.0, uv=0.0, expect=""):
    fixture.render_to_universe(
        universe,
        brightness=brightness,
        hue=hue,
        saturation=sat,
        value=val,
        strobe=strobe,
        white=white,
        amber=amber,
        uv=uv,
    )
    _send_and_log(out, universe, fixture, label)
    if expect:
        print(f"         Expected: {expect}")
    result = _confirm("Observed?")
    _log(f"  RESULT  {label}: {result.upper()}")
    return result


def _raw_channels(universe, address, overrides: dict):
    """Write arbitrary raw channel values for testing sub-sections."""
    for offset, val in overrides.items():
        universe.set_channel(address + offset, val)


def main():
    parser = argparse.ArgumentParser(
        description="Chauvet GigBAR Move+ILS hardware validation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--port",    required=True, type=str,
                        help="serial port (e.g. /dev/ttyUSB0 or COM3)")
    parser.add_argument("--address", type=int, default=17,
                        help="fixture DMX start address (default 17)")
    args = parser.parse_args()

    print()
    print("=" * 64)
    print("  LIGHTBRAIN — Chauvet GigBAR Move+ILS Hardware Validation")
    print("=" * 64)
    print(f"  Port      : {args.port}")
    print(f"  Address   : {args.address}  (channels {args.address}–{args.address + 28})")
    print(f"  Personality: 29Ch (must be set on the fixture)")
    print()
    print("  !! Confirm 29Ch personality before proceeding !!")
    print("  !! Clear 1m radius around spot head !!")
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
    fixture  = ChauvetGigBarMoveILS(
        fixture_id="gigbar_val",
        name="GigBAR Validation",
        dmx_address=args.address,
    )
    addr = args.address
    results = {}

    try:
        # ── Step 1: Blackout ──────────────────────────────────────────────
        print("[1] Blackout — all channels zero")
        universe.blackout()
        out.send(universe)
        time.sleep(0.3)
        out.send(universe)
        _log(f"Blackout sent (ch {addr}–{addr+28} all zero)")
        result = _confirm("All sections dark?")
        results["blackout"] = result
        _log(f"  RESULT  blackout: {result.upper()}")
        time.sleep(0.5)

        # ── Step 2: Par — Red ─────────────────────────────────────────────
        print("\n[2] Par wash — Red (Ch1=255, Ch2-6=0)")
        result = _step_render(out, universe, fixture, "par_red",
                              brightness=1.0, hue=0.0, sat=1.0, val=1.0,
                              expect="Par wash LEDs solid red; derby/spot may light")
        results["par_red"] = result
        time.sleep(HOLD_S)

        # ── Step 3: Par — White + Amber ───────────────────────────────────
        print("\n[3] Par wash — White + Amber channels (Ch5 + Ch4 active)")
        result = _step_render(out, universe, fixture, "par_white_amber",
                              brightness=1.0, hue=30.0, sat=0.1, val=1.0,
                              white=1.0, amber=0.6,
                              expect="Par wash warm white (W+A channels lit)")
        results["par_white_amber"] = result
        time.sleep(HOLD_S)

        # ── Step 4: Par — UV ──────────────────────────────────────────────
        print("\n[4] Par wash — UV (Ch6=255, others=0)")
        result = _step_render(out, universe, fixture, "par_uv",
                              brightness=1.0, hue=0.0, sat=0.0, val=0.0,
                              uv=1.0,
                              expect="UV LEDs lit (blue-purple glow)")
        results["par_uv"] = result
        time.sleep(HOLD_S)

        # ── Step 5: Derby section ─────────────────────────────────────────
        print("\n[5] Derby — rotation (Ch13=64 = slow CW)")
        print("         Expected: derby head spinning slowly clockwise")
        universe.blackout()
        _raw_channels(universe, addr, {
            0: 0,     # par off
            7: 200,   # derby red
            8: 200,   # derby green
            9: 200,   # derby blue
            12: 64,   # slow CW rotation
        })
        _send_and_log(out, universe, fixture, "derby_cw_rotation")
        result = _confirm("Derby spinning clockwise with colored beams?")
        results["derby_rotation"] = result
        _log(f"  RESULT  derby_rotation: {result.upper()}")
        time.sleep(HOLD_S)

        # Stop derby rotation
        universe.blackout()
        out.send(universe)
        time.sleep(0.3)

        # ── Step 6: Flash LEDs ────────────────────────────────────────────
        print("\n[6] Flash LEDs — all four at half brightness (Ch14–17=128)")
        print("         Expected: four small white flash LEDs lit around rig")
        universe.blackout()
        for offset in range(13, 17):   # Ch14–17 = offsets 13–16
            universe.set_channel(addr + offset, 128)
        out.send(universe)
        _log(f"Flash LEDs: ch{addr+13}–{addr+16} = 128")
        result = _confirm("Flash LEDs lit (four small white LEDs)?")
        results["flash_leds"] = result
        _log(f"  RESULT  flash_leds: {result.upper()}")
        time.sleep(HOLD_S)
        universe.blackout()
        out.send(universe)
        time.sleep(0.3)

        # ── Step 7: Spot head — center aim ───────────────────────────────
        print("\n[7] Spot head — center aim + full dimmer")
        print("         Expected: spot head moves to centre, beam visible on ceiling/floor")
        fixture.set_spot_aim(pan_deg=270, tilt_dmx=128)   # centre
        universe.blackout()
        fixture.render_to_universe(
            universe,
            brightness=1.0, hue=0.0, sat=0.0, val=1.0,
        )
        out.send(universe)
        _log(f"Spot: centre aim, Ch{addr+27}=255 (dimmer)")
        result = _confirm("Spot head aimed to centre, beam visible?")
        results["spot_centre"] = result
        _log(f"  RESULT  spot_centre: {result.upper()}")
        time.sleep(HOLD_S)

        # ── Step 8: Spot head — pan sweep ────────────────────────────────
        print("\n[8] Spot head — pan sweep left → right")
        print("         Expected: beam sweeps horizontally across the room")
        for step in range(FADE_STEPS + 1):
            pan = step / FADE_STEPS * 540
            fixture.set_spot_aim(pan_deg=pan, tilt_dmx=128)
            fixture.render_to_universe(universe, brightness=1.0, hue=0.0, sat=0.0, val=1.0)
            out.send(universe)
            time.sleep(FADE_STEP_S)
        result = _confirm("Beam swept smoothly across room?")
        results["spot_pan_sweep"] = result
        _log(f"  RESULT  spot_pan_sweep: {result.upper()}")

        # Return to centre
        fixture.set_spot_aim(pan_deg=270, tilt_dmx=128)
        fixture.render_to_universe(universe, brightness=1.0, hue=0.0, sat=0.0, val=1.0)
        out.send(universe)
        time.sleep(0.5)

        # ── Step 9: Laser (explicit opt-in) ──────────────────────────────
        print("\n[9] Laser — SAFETY OPT-IN required")
        print("         The laser will activate if you proceed.")
        print("         Confirm:")
        print("           [ ] Area is clear of people at eye level")
        print("           [ ] No reflective surfaces pointing at people")
        print("           [ ] Local regulations permit laser use")
        confirm_laser = input(
            "         Type 'YES LASER' to enable laser test, or Enter to skip: "
        ).strip()
        if confirm_laser == "YES LASER":
            universe.blackout()
            fixture.enable_laser(True)
            fixture.render_to_universe(universe, brightness=1.0, hue=0.0, sat=0.0, val=1.0)
            out.send(universe)
            _log(f"Laser enabled: Ch{addr+18} (control) active")
            result = _confirm("Laser patterns visible?")
            results["laser"] = result
            _log(f"  RESULT  laser: {result.upper()}")
            time.sleep(HOLD_S)
            fixture.enable_laser(False)
        else:
            _log("  RESULT  laser: SKIPPED")
            results["laser"] = "skip"

        # ── Step 10: Par strobe ───────────────────────────────────────────
        print("\n[10] Par strobe — mid speed (Ch7≈128)")
        print("          !! Strobe will fire !!")
        ans = input("          Proceed? [y/skip]: ").strip().lower()
        if ans == "y":
            fixture.enable_laser(False)
            result = _step_render(out, universe, fixture, "par_strobe_mid",
                                  brightness=1.0, hue=0.0, sat=0.0, val=1.0,
                                  strobe=0.5,
                                  expect="Par wash strobing at ~10 Hz white")
            results["par_strobe"] = result
            time.sleep(HOLD_S)
        else:
            _log("  RESULT  par_strobe: SKIPPED")
            results["par_strobe"] = "skip"

    except KeyboardInterrupt:
        _log("Aborted by operator")
    finally:
        fixture.enable_laser(False)
        fixture.set_derby_enabled(False)
        universe.blackout()
        try:
            out.send(universe)
            time.sleep(0.1)
            out.send(universe)
        except Exception:
            pass
        out.close()
        _log("Serial port closed — all sections dark")

    # ── Summary ───────────────────────────────────────────────────────────
    print()
    print("=" * 64)
    print("  VALIDATION SUMMARY — GigBAR Move+ILS")
    print(f"  Port {args.port}  ·  Address {args.address}")
    print("=" * 64)
    fails  = [k for k, v in results.items() if v == "fail"]
    skips  = [k for k, v in results.items() if v == "skip"]
    for step, res in results.items():
        marker = {"pass": "PASS", "fail": "FAIL", "skip": "SKIP"}.get(res, "?")
        print(f"  {marker}  {step}")
    print()
    if fails:
        print(f"  FAILURES: {', '.join(fails)}")
        print("  → Document in docs/HARDWARE_VALIDATION_LOG.md")
    elif skips:
        print(f"  Passed with {len(skips)} step(s) skipped")
    else:
        print("  All steps passed.")
    print()


if __name__ == "__main__":
    main()
