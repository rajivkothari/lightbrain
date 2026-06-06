"""
Mock DMX output — prints changed channels to stdout and logs them.

Used for development and testing without physical DMX hardware.
The terminal_debug overlay calls render_fixture_summary() directly
for the formatted display rather than raw channel dumps.
"""

import time
from typing import Optional

from dmx.universe import DMXUniverse


class MockDMXOutput:
    """
    Accepts a DMX universe and logs changed channels.

    For the terminal overlay, use render_fixture_summary() which
    returns a dict of named channels for a specific fixture.
    """

    def __init__(self, verbose: bool = False):
        self.verbose      = verbose  # print every changed channel if True
        self._prev        = DMXUniverse()
        self._frame_count = 0
        self._last_log    = 0.0

    def send(self, universe: DMXUniverse) -> None:
        """
        Compare universe against previous state and log changed channels.

        In verbose mode logs every frame; otherwise logs only when channels
        actually change (reduces console spam during silence).
        """
        self._frame_count += 1
        changes = universe.changed_channels(self._prev)

        if changes and self.verbose:
            now = time.monotonic()
            print(f"[DMX MOCK] frame={self._frame_count} "
                  f"changed={len(changes)}")
            for ch, old, new in changes:
                print(f"  ch{ch:03d}: {old:3d} → {new:3d}")

        self._prev = universe.copy()

    def get_channel_map(self, start_address: int,
                        num_channels: int) -> dict:
        """
        Return {channel_number: value} for a fixture's address range.
        Useful for diagnostic overlays.
        """
        return {
            ch: self._prev.get_channel(ch)
            for ch in range(start_address,
                            min(start_address + num_channels, 513))
        }

    @property
    def frame_count(self) -> int:
        return self._frame_count

    def close(self) -> None:
        pass  # nothing to close

    @property
    def output_type(self) -> str:
        return "MOCK"
