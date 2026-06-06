"""
List all available sounddevice audio input and output devices.

Run from repo root:
  python scripts/list_audio_devices.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sounddevice as sd


def main():
    print("\n" + "=" * 60)
    print("  LIGHTBRAIN — Available Audio Devices")
    print("=" * 60)

    devices = sd.query_devices()
    default_in  = sd.default.device[0]
    default_out = sd.default.device[1]

    print(f"\n{'IDX':<5} {'TYPE':<8} {'NAME':<36} {'IN':<4} {'OUT':<4} {'SR':<8}")
    print("-" * 60)

    for i, dev in enumerate(devices):
        in_ch  = dev["max_input_channels"]
        out_ch = dev["max_output_channels"]
        sr     = int(dev["default_samplerate"])
        kind   = []
        if in_ch  > 0: kind.append("IN")
        if out_ch > 0: kind.append("OUT")
        kind_str = "+".join(kind) if kind else "—"
        default_marker = ""
        if i == default_in:  default_marker += " ◄ default in"
        if i == default_out: default_marker += " ◄ default out"
        name = dev["name"][:35]
        print(f"[{i:3d}] {kind_str:<8} {name:<36} {in_ch:<4} {out_ch:<4} {sr:<8} {default_marker}")

    print("\nTo use a specific input device, pass --device N to the main script.")
    print("For virtual loopback (DJ monitor): install BlackHole (macOS) or")
    print("  VB-Audio Cable (Windows), then select its device index above.\n")


if __name__ == "__main__":
    main()
