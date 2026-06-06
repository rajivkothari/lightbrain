"""
Base fixture class. All fixture types inherit from this.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class FixtureBase:
    """Common fields for all DMX fixtures."""
    fixture_id:   str
    name:         str
    dmx_address:  int   # 1-indexed DMX start address
    lane:         str = "room"
    group:        str = "all"

    def render_to_universe(self, universe, **kwargs) -> None:
        """Subclasses implement this to write their channels."""
        raise NotImplementedError
