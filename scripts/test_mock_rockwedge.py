"""
Full mock pipeline test: audio → analysis → beat detection → smoothing →
room lane (with WAU channels) → RockWedge mapper → mock DMX → terminal overlay.

No physical DMX hardware required.

Run from repo root:
  python scripts/test_mock_rockwedge.py --demo        # synthetic audio (no mic)
  python scripts/test_mock_rockwedge.py --simulate    # same as --demo
  python scripts/test_mock_rockwedge.py --device 2    # real mic, device index 2
  python scripts/test_mock_rockwedge.py --demo --mode banger

New in Sprint 2:
  - Ch5 White, Ch6 Amber, Ch7 UV driven from palette-mode rules
  - Beat detection with BPM shown in overlay
  - Smooth WAU crossfade on mode switch (Banger = instant snap)
  - MIDI CC input (requires mido[ports-rtmidi])

Keyboard controls (when running in a real terminal):
  O = Open Dance   D = Dinner   B = Banger
  I = Indian/Latin S = Speech   L = Slow Dance
  Space = Blackout toggle       Q = Quit
"""

import sys
import os
import argparse
import time
import colorsys
import queue as _queue
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audio.analyzer      import AudioAnalyzer, AudioBands
from audio.synthetic     import SyntheticAudioSource
from audio.beat_detector import BeatDetector

from engine.smoothing    import LaneSmoother
from engine.palettes     import load_all_palettes
from engine.lanes        import RoomLane
from engine.modes        import get_mode, KEYBOARD_MAP
from engine.safety       import SafetyEngine
from engine.transitions  import ModeTransitioner

from dmx.universe    import DMXUniverse
from dmx.output_mock import MockDMXOutput

from fixtures.rockwedge import RockWedge

from ui.terminal_debug import TerminalDebugOverlay

try:
    from midi.input import MidiInput
except ImportError:
    MidiInput = None


ROOT         = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PALETTES_DIR = os.path.join(ROOT, "config", "palettes")
TARGET_FPS   = 40
FRAME_TIME   = 1.0 / TARGET_FPS


# ---------------------------------------------------------------------------
# Non-blocking keyboard input (Unix only)
# ---------------------------------------------------------------------------

_key_queue: _queue.Queue = _queue.Queue()
_kbd_stop  = threading.Event()


def _keyboard_thread_fn() -> None:
    """Background thread: single-char reads from stdin in cbreak mode."""
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
    parser = argparse.ArgumentParser(description="LightBrain mock pipeline Sprint 2")
    parser.add_argument("--device",  type=int, default=None,
                        help="sounddevice input device index")
    parser.add_argument("--palette", type=str, default=None,
                        help="starting palette key (default: follows --mode)")
    parser.add_argument("--mode",    type=str, default="open_dance",
                        help="starting mode key (default: open_dance)")
    parser.add_argument("--demo",    "--simulate", dest="demo",
                        action="store_true",
                        help="use synthetic audio — no microphone needed")
    parser.add_argument("--midi",    type=str, default=None,
                        help="MIDI input port name (default: first available)")
    args = parser.parse_args()

    # ---- palettes ----
    all_palettes = load_all_palettes(PALETTES_DIR)
    if not all_palettes:
        print("[ERROR] No palettes found. Run from the repo root directory.")
        sys.exit(1)

    current_mode = get_mode(args.mode)
    palette_key  = args.palette or current_mode.palette_key
    if palette_key not in all_palettes:
        palette_key = "open_dance"
    current_palette = all_palettes[palette_key]

    # ---- audio source ----
    error_msg = None

    if args.demo:
        capture      = SyntheticAudioSource(block_size=1024)
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
    analyzer      = AudioAnalyzer()
    beat_detector = BeatDetector()
    smoother      = LaneSmoother()
    safety        = SafetyEngine()
    safety.update_from_mode(current_mode)
    room_lane     = RoomLane(current_palette, mode=current_mode)
    transitioner  = ModeTransitioner(current_mode)

    # ---- fixture + DMX ----
    rockwedge = RockWedge(fixture_id="rw_001", name="RockWedge LED", dmx_address=1)
    universe  = DMXUniverse()
    dmx_out   = MockDMXOutput(verbose=False)

    # ---- MIDI ----
    midi_in = None
    if MidiInput is not None:
        midi_in = MidiInput(port_name=args.midi)
        if not midi_in.open():
            midi_in = None

    overlay    = TerminalDebugOverlay()
    overlay.init_screen()
    kbd_thread = _start_keyboard()

    # Sprint 2: WAU crossfade snapshot
    _wau_snapshot = (0.0, 0.0, 0.0)
    last_lanes: dict = {}
    quit_flag = False

    try:
        while not quit_flag:
            t0 = time.monotonic()

            # ---- keyboard events ----
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
                        new_mode    = get_mode(mode_key)
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

            # ---- MIDI events ----
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
                    elif evt.type == "blackout":
                        if evt.value > 0.5:
                            if not safety.state.blackout_active:
                                safety.toggle_blackout()
                        else:
                            if safety.state.blackout_active:
                                safety.toggle_blackout()

            if quit_flag:
                break

            # ---- audio ----
            block = capture.get_latest_block()
            bands = analyzer.analyze(block) if block is not None else AudioBands()

            # ---- beat detection ----
            beat_detected, _ = beat_detector.update(
                bands.as_dict().get("low_energy", 0.0)
            )

            # ---- smoothing ----
            last_lanes = smoother.update(bands.as_dict())
            last_lanes["bpm"]  = beat_detector.bpm
            last_lanes["beat"] = beat_detected

            # ---- room lane ----
            room_out = room_lane.render(
                smoothed_room=last_lanes["room"],
                impact=last_lanes["impact"],
                safety=safety,
                beat_trigger=beat_detected,
            )

            # ---- WAU crossfade ----
            blend_t = transitioner.update()
            if blend_t < 1.0:
                sw, sa, su = _wau_snapshot
                eff_white = sw + (room_out.white - sw) * blend_t
                eff_amber = sa + (room_out.amber - sa) * blend_t
                eff_uv    = su + (room_out.uv    - su) * blend_t
            else:
                eff_white, eff_amber, eff_uv = room_out.white, room_out.amber, room_out.uv

            last_lanes["wau_white"] = eff_white
            last_lanes["wau_amber"] = eff_amber
            last_lanes["wau_uv"]    = eff_uv

            # ---- fixture write ----
            rockwedge.render_to_universe(
                universe,
                brightness=1.0,
                hue=room_out.hsv.h,
                saturation=room_out.hsv.s,
                value=room_out.hsv.v,
                strobe=room_out.strobe,
                white=eff_white,
                amber=eff_amber,
                uv=eff_uv,
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
                dmx_channels=ch_vals,
                dmx_output_type="MOCK",
                safety_blackout=safety.state.blackout_active,
                safety_strobe_ok=safety.state.strobe_allowed,
                error=error_msg or getattr(capture, "last_error", None),
            )

            # ---- frame rate cap ----
            elapsed   = time.monotonic() - t0
            remaining = FRAME_TIME - elapsed
            if remaining > 0:
                time.sleep(remaining)

    except KeyboardInterrupt:
        pass
    finally:
        _stop_keyboard(kbd_thread)
        if midi_in is not None:
            midi_in.close()
        universe.blackout()
        dmx_out.send(universe)
        capture.stop()
        overlay.restore_screen()
        print("Mock test stopped.")


if __name__ == "__main__":
    main()
