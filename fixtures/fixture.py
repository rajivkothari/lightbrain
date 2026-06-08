"""
Base fixture class and DMX address collision checker.

Hardware day workflow
---------------------
1. Set the DMX start address and personality on each physical fixture.
2. Connect wireless DMX receivers to each fixture; pair the transmitter
   to the DMXking USB Pro output — the wireless link is transparent.
3. Update ``config/rig_config.json``:
   - Replace the ``fixtures`` array with the ``_hardware_fixtures`` block.
   - Set ``dmx.output`` to ``"enttec_pro"`` and ``dmx.serial_port`` to
     the USB adapter port (e.g. ``"/dev/ttyUSB0"`` on Linux,
     ``"COM3"`` on Windows).
   - Verify each ``dmx_address`` matches the address programmed in step 1.
4. Run ``python -m app.main`` — ``check_dmx_address_map()`` validates the
   layout at startup and raises ``ValueError`` on any overlap or out-of-
   bounds address before the first DMX frame is sent.
"""

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class FixtureBase:
    """Common fields for all DMX fixtures."""
    fixture_id:   str
    name:         str
    dmx_address:  int   # 1-indexed DMX start address
    lane:         str = "room"
    group:        str = "all"

    @property
    def channel_count(self) -> int:
        """Number of consecutive DMX channels this fixture occupies."""
        raise NotImplementedError

    def render_to_universe(self, universe, **kwargs) -> None:
        """Subclasses implement this to write their channels."""
        raise NotImplementedError


def check_dmx_address_map(fixtures: List["FixtureBase"]) -> None:
    """
    Validate that no two fixtures share a DMX channel and none exceed ch 512.

    Raises ``ValueError`` with a human-readable layout table on any conflict
    so the operator can correct ``rig_config.json`` before touching hardware.

    Called automatically at startup in ``app/main.py``.
    """
    occupied: dict[int, str] = {}  # channel → fixture name
    for fx in fixtures:
        start = fx.dmx_address
        count = fx.channel_count
        end   = start + count - 1

        if start < 1:
            raise ValueError(
                f"Fixture '{fx.name}' has dmx_address {start} — "
                f"DMX addresses are 1-indexed (minimum 1)."
            )
        if end > 512:
            raise ValueError(
                f"Fixture '{fx.name}' at address {start} occupies "
                f"channels {start}–{end}, which exceeds the 512-channel "
                f"DMX universe limit.\n"
                f"Reduce dmx_address or switch to a narrower personality."
            )
        for ch in range(start, end + 1):
            if ch in occupied:
                raise ValueError(
                    f"DMX address collision: channel {ch} is claimed by both "
                    f"'{occupied[ch]}' and '{fx.name}'.\n"
                    f"Current layout:\n{_format_layout(fixtures)}"
                )
            occupied[ch] = fx.name


def _format_layout(fixtures: List["FixtureBase"]) -> str:
    """Return a compact address-map table for error messages."""
    lines = ["  Fixture                    Start  Channels  End"]
    lines.append("  " + "-" * 48)
    for fx in fixtures:
        start = fx.dmx_address
        count = fx.channel_count
        lines.append(
            f"  {fx.name:<26}  {start:>4}  {count:>6} ch  {start + count - 1:>4}"
        )
    return "\n".join(lines)
