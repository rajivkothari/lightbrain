"""
Test the audio analyzer in isolation — prints raw band energies.

Run from repo root:
  python scripts/test_audio_analyzer.py [--device N] [--duration 10]
  python scripts/test_audio_analyzer.py --demo     # no microphone needed

Press Ctrl+C to stop.
"""

import sys
import os
import argparse
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audio.input     import list_input_devices
from audio.analyzer  import AudioAnalyzer
from audio.synthetic import SyntheticAudioSource


def bar(v: float, w: int = 30) -> str:
    filled = int(max(0.0, min(1.0, v)) * w)
    return "[" + "#" * filled + "." * (w - filled) + "]"


def main():
    parser = argparse.ArgumentParser(description="LightBrain audio analyzer test")
    parser.add_argument("--device",   type=int, default=None,
                        help="sounddevice input device index")
    parser.add_argument("--duration", type=int, default=0,
                        help="run for N seconds (0 = run forever)")
    parser.add_argument("--demo",     action="store_true",
                        help="use synthetic audio — no microphone needed")
    args = parser.parse_args()

    # ---- print available devices ----
    try:
        devices = list_input_devices()
    except Exception as e:
        devices = []
        print(f"[warn] Could not query audio devices: {e}")

    if devices:
        print("\nAvailable input devices:")
        for d in devices:
            print(f"  [{d['index']:3d}] {d['name']}")
    else:
        print("\nNo input devices found.")
        if not args.demo:
            print("  → Re-run with --demo to use synthetic audio instead.\n")

    print()

    # ---- choose audio source ----
    if args.demo:
        capture = SyntheticAudioSource(block_size=1024)
        print("Audio source: SYNTHETIC (demo mode)\n")
    else:
        from audio.input import AudioCapture
        capture = AudioCapture(device_index=args.device, block_size=1024)
        try:
            capture.start()
            print(f"Audio source: {capture.device_name}\n")
        except Exception as e:
            print(f"[error] Could not open audio device: {e}")
            print("  → Re-run with --demo for synthetic audio.\n")
            sys.exit(1)

    if args.demo:
        capture.start()

    analyzer = AudioAnalyzer(sample_rate=44100, block_size=1024)

    print("Press Ctrl+C to stop.\n")
    print("LOW                                MID                                HIGH                               ENERGY")

    start = time.monotonic()
    try:
        while True:
            if args.duration > 0 and (time.monotonic() - start) > args.duration:
                break

            block = capture.get_latest_block()
            if block is None:
                time.sleep(0.01)
                continue

            bands = analyzer.analyze(block)

            sys.stdout.write("\r")
            sys.stdout.write(
                f"{bar(bands.low_energy)}  {bands.low_energy:.3f}  "
                f"{bar(bands.mid_energy)}  {bands.mid_energy:.3f}  "
                f"{bar(bands.high_energy)}  {bands.high_energy:.3f}  "
                f"{bar(bands.overall_energy)}  {bands.overall_energy:.3f}   "
            )
            sys.stdout.flush()
            time.sleep(0.025)

    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        capture.stop()


if __name__ == "__main__":
    main()
