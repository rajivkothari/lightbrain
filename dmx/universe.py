"""
512-channel DMX universe.

Channels are 1-indexed in the public API (DMX standard) but stored
internally as a 0-indexed numpy array for performance.
"""

import numpy as np
from typing import List, Optional


class DMXUniverse:
    """
    Represents a 512-channel DMX universe.

    Public channel numbers are 1–512 (DMX standard).
    All values are clamped to 0–255.
    """

    UNIVERSE_SIZE = 512

    def __init__(self):
        self._channels = np.zeros(self.UNIVERSE_SIZE, dtype=np.uint8)

    # ------------------------------------------------------------------
    # Write / read
    # ------------------------------------------------------------------

    def set_channel(self, channel: int, value: int) -> None:
        """Set channel (1-indexed) to value (0–255)."""
        if not (1 <= channel <= self.UNIVERSE_SIZE):
            raise ValueError(f"DMX channel must be 1–512, got {channel}")
        self._channels[channel - 1] = max(0, min(255, int(value)))

    def get_channel(self, channel: int) -> int:
        """Get the current value of channel (1-indexed)."""
        if not (1 <= channel <= self.UNIVERSE_SIZE):
            raise ValueError(f"DMX channel must be 1–512, got {channel}")
        return int(self._channels[channel - 1])

    def set_channels(self, start_channel: int,
                     values: List[int]) -> None:
        """Set a block of channels starting at start_channel (1-indexed)."""
        for i, v in enumerate(values):
            ch = start_channel + i
            if ch > self.UNIVERSE_SIZE:
                break
            self.set_channel(ch, v)

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def blackout(self) -> None:
        """Set all channels to 0."""
        self._channels[:] = 0

    def to_bytes(self) -> bytes:
        """Return the full 512-byte universe as a bytes object."""
        return self._channels.tobytes()

    def copy(self) -> "DMXUniverse":
        """Return a deep copy of this universe."""
        new_u = DMXUniverse()
        new_u._channels = self._channels.copy()
        return new_u

    def changed_channels(self, previous: "DMXUniverse") -> List[tuple]:
        """
        Return list of (channel, old_value, new_value) for channels that
        differ from the previous universe snapshot.
        """
        diff_indices = np.where(self._channels != previous._channels)[0]
        return [
            (int(i + 1), int(previous._channels[i]), int(self._channels[i]))
            for i in diff_indices
        ]

    def load_snapshot(self, data: bytes) -> None:
        """Overwrite all 512 channels from a raw bytes snapshot (must be 512 bytes)."""
        self._channels[:] = np.frombuffer(data, dtype=np.uint8)

    def snapshot(self) -> np.ndarray:
        """Return a copy of the raw channel array (0-indexed)."""
        return self._channels.copy()

    def __repr__(self) -> str:
        active = [(i + 1, int(v))
                  for i, v in enumerate(self._channels) if v > 0]
        return f"<DMXUniverse active_channels={active}>"
