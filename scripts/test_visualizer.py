"""
LightBrain Preview Visualizer

Shows a 2D animated rig preview that reacts to live audio or simulated music.

Usage:
  python scripts/test_visualizer.py --simulate        # simulated audio (no mic)
  python scripts/test_visualizer.py                   # try live audio, fall back to simulate
  python scripts/test_visualizer.py --device N        # specific audio device
  python scripts/test_visualizer.py --simulate --mode banger

Keyboard controls:
  O = Open Dance   D = Dinner   B = Banger
  I = Indian/Latin S = Speech   L = Slow Dance
  Space = Blackout toggle
  A     = toggle Simulated / Live audio
  P     = pause/unpause animation
  Q/Esc = Quit
"""

import argparse
import colorsys
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame

from audio.analyzer  import AudioAnalyzer, AudioBands
from audio.synthetic import SyntheticAudioSource

from engine.smoothing import LaneSmoother
from engine.palettes  import load_all_palettes
from engine.lanes     import RoomLane
from engine.modes     import get_mode, KEYBOARD_MAP
from engine.safety    import SafetyEngine

from app.render.scene      import SceneLayout, WINDOW_W, WINDOW_H
from app.render.visualizer import Visualizer


ROOT         = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PALETTES_DIR = os.path.join(ROOT, "config", "palettes")

TARGET_FPS  = 40
FRAME_TIME  = 1.0 / TARGET_FPS


# ---------------------------------------------------------------------------
# Audio source helpers
# ---------------------------------------------------------------------------

def _try_open_live_audio(device_index):
    """Try to open a real audio capture; return (capture, label) or raise."""
    from audio.input import AudioCapture
    cap = AudioCapture(device_index=device_index, block_size=1024)
    cap.start()
    return cap, (cap.device_name or f"device {device_index}")


def _open_simulated():
    src = SyntheticAudioSource(block_size=1024)
    src.start()
    return src, "Synthetic (demo)"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="LightBrain Preview Visualizer")
    parser.add_argument("--simulate", "--demo", dest="simulate",
                        action="store_true",
                        help="use synthetic audio — no microphone needed")
    parser.add_argument("--device", type=int, default=None,
                        help="sounddevice device index for live audio")
    parser.add_argument("--mode", type=str, default="open_dance",
                        help="starting mode key")
    args = parser.parse_args()

    # ---- palettes ----
    all_palettes = load_all_palettes(PALETTES_DIR)
    if not all_palettes:
        print("[ERROR] No palettes found. Run from the repo root directory.")
        sys.exit(1)

    # ---- mode init ----
    current_mode    = get_mode(args.mode)
    current_palette = all_palettes.get(current_mode.palette_key, list(all_palettes.values())[0])

    # ---- engine ----
    analyzer  = AudioAnalyzer()
    smoother  = LaneSmoother()
    safety    = SafetyEngine()
    safety.update_from_mode(current_mode)
    room_lane = RoomLane(current_palette, mode=current_mode)

    # ---- audio source ----
    if args.simulate:
        capture, audio_label = _open_simulated()
        using_live = False
    else:
        try:
            capture, audio_label = _try_open_live_audio(args.device)
            using_live = True
        except Exception as e:
            print(f"[Audio] Live audio failed ({e}), falling back to simulated mode.")
            capture, audio_label = _open_simulated()
            using_live = False

    # ---- pygame ----
    pygame.init()
    screen  = pygame.display.set_mode((WINDOW_W, WINDOW_H))
    pygame.display.set_caption("LightBrain Preview Visualizer")
    clock   = pygame.time.Clock()

    scene = SceneLayout()
    vis   = Visualizer()
    vis.init()

    paused    = False
    quit_flag = False
    fps_val   = 0.0

    # Freeze frame state when paused
    last_rig = None

    try:
        while not quit_flag:
            dt = clock.tick(TARGET_FPS) / 1000.0

            # ---- pygame events ----
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    quit_flag = True

                elif event.type == pygame.KEYDOWN:
                    key = pygame.key.name(event.key).lower()

                    if key in ("q", "escape"):
                        quit_flag = True

                    elif key == "p":
                        paused = not paused

                    elif key == "a":
                        # Toggle simulated / live audio
                        capture.stop()
                        if using_live:
                            capture, audio_label = _open_simulated()
                            using_live = False
                        else:
                            try:
                                capture, audio_label = _try_open_live_audio(args.device)
                                using_live = True
                            except Exception as ex:
                                print(f"[Audio] Live audio failed: {ex}")
                                capture, audio_label = _open_simulated()
                                using_live = False

                    elif key == "space":
                        safety.toggle_blackout()

                    else:
                        # Mode keys: o d b i s l
                        mode_key = KEYBOARD_MAP.get(key)
                        if mode_key and mode_key not in ("quit", "blackout"):
                            current_mode    = get_mode(mode_key)
                            current_palette = all_palettes.get(
                                current_mode.palette_key, current_palette
                            )
                            safety.update_from_mode(current_mode)
                            room_lane.set_mode(current_mode)
                            room_lane.set_palette(current_palette)
                        elif mode_key == "blackout":
                            safety.toggle_blackout()

            if quit_flag:
                break

            # ---- engine tick (skipped when paused) ----
            if not paused:
                block = capture.get_latest_block()
                bands = analyzer.analyze(block) if block is not None else AudioBands()

                lanes = smoother.update(bands.as_dict())

                room_out = room_lane.render(
                    smoothed_room=lanes["room"],
                    impact=lanes["impact"],
                    safety=safety,
                )

                last_rig = scene.update_and_build(
                    bands=bands.as_dict(),
                    lanes=lanes,
                    hue=room_out.hsv.h,
                    saturation=room_out.hsv.s,
                    brightness=room_out.brightness,
                    base_brt=room_out.base_brightness,
                    pulse_brt=room_out.pulse_brightness,
                    mode_key=current_mode.key,
                    palette_name=room_lane.palette_name,
                    blackout=safety.state.blackout_active,
                )

            # ---- render ----
            if last_rig is not None:
                audio_mode_label = ("LIVE" if using_live else "SIMULATED")
                if paused:
                    audio_mode_label += " [PAUSED]"
                vis.draw(screen, last_rig, fps=fps_val, audio_mode=audio_mode_label)
                pygame.display.flip()

            fps_val = clock.get_fps()

    except KeyboardInterrupt:
        pass
    finally:
        capture.stop()
        pygame.quit()
        print("Visualizer stopped.")


if __name__ == "__main__":
    main()
