"""
Test the audio analyzer in isolation — prints raw band energies.

Run from repo root:
  python scripts/test_audio_analyzer.py [--device N] [--duration 10]

Press Ctrl+C to stop.
"""

import sys
import os
import argparse
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audio.input    import AudioCapture, list_input_devices
from audio.analyzer import AudioAnalyzer


def bar(v: float, w: int = 30) -> str:
    filled = int(max(0.0, min(1.0, v)) * w)
    return "[" + "#" * filled + "." * (w - filled) + "]"


def main():
    parser = argparse.ArgumentParser(description="LightBrain audio analyzer test")
    parser.add_argument("--device",   type=int, default=None, help="input device index")
    parser.add_argument("--duration", type=int, default=0,    help="run seconds (0=forever)")
    args = parser.parse_args()

    print("\nAvailable input devices:")
    for d in list_input_devices():
        print(f"  [{d['index']:3d}] {d['name']}")
    print()

    capture  = AudioCapture(device_index=args.device, block_size=1024)
    analyzer = AudioAnalyzer(sample_rate=44100, block_size=1024)

    print(f"Starting capture on device: {args.device or 'default'}")
    print("Press Ctrl+C to stop.\n")

    capture.start()
    print(f"  Device: {capture.device_name}\n")

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
                f"LOW {bar(bands.low_energy)} {bands.low_energy:.3f}  "
                f"MID {bar(bands.mid_energy)} {bands.mid_energy:.3f}  "
                f"HI {bar(bands.high_energy)} {bands.high_energy:.3f}  "
                f"E {bar(bands.overall_energy)} {bands.overall_energy:.3f}   "
            )
            sys.stdout.flush()
            time.sleep(0.025)

    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        capture.stop()


if __name__ == "__main__":
    main()
