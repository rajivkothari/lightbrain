"""
Fixture Aiming Tool — Sprint 7.

Interactive pan/tilt control for moving-head fixtures during rig setup.
Lets the operator nudge a fixture to aim at a target, then save the
position as a named preset in fixtures/positions.json.

Typical workflow:
    tool = FixtureAimingTool(fixture, universe)
    tool.set_pan(0.0)        # point centre
    tool.set_tilt(110)       # tilt toward dance floor
    tool.nudge_pan(5.0)      # adjust right
    tool.save_position("cake_table", "Aimed at cake table")
"""

import json
import os
from typing import Optional

from fixtures.djflx_beam import (
    DJFLXBeam,
    pan_degrees_to_dmx,
    PAN_RANGE_DEG,
    DEFAULT_TILT,
    CH_DIMMER,
)
from dmx.universe import DMXUniverse

_DEFAULT_POSITIONS_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "fixtures",
    "positions.json",
)

# DMX dimmer value used while aiming so the beam is visible (50%)
_AIM_DIMMER = 128


class FixtureAimingTool:
    """
    Manual pan/tilt control for a single DJFLXBeam during rig setup.

    Pan is specified in degrees (±PAN_RANGE_DEG).
    Tilt is specified as a raw DMX value (0–255).
    """

    def __init__(
        self,
        fixture:        DJFLXBeam,
        universe:       DMXUniverse,
        positions_file: str = _DEFAULT_POSITIONS_FILE,
    ) -> None:
        self._fixture        = fixture
        self._universe       = universe
        self._positions_file = positions_file
        self._pan_deg:  float = 0.0
        self._tilt_dmx: int   = DEFAULT_TILT

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def pan_deg(self) -> float:
        return self._pan_deg

    @property
    def tilt_dmx(self) -> int:
        return self._tilt_dmx

    # ------------------------------------------------------------------
    # Movement
    # ------------------------------------------------------------------

    def set_pan(self, degrees: float) -> None:
        """Set absolute pan position in degrees (clamped to ±PAN_RANGE_DEG)."""
        self._pan_deg = max(-PAN_RANGE_DEG, min(PAN_RANGE_DEG, degrees))
        self._flush()

    def set_tilt(self, dmx: int) -> None:
        """Set absolute tilt as DMX value 0–255."""
        self._tilt_dmx = max(0, min(255, dmx))
        self._flush()

    def nudge_pan(self, delta_deg: float) -> None:
        """Nudge pan by delta_deg degrees."""
        self.set_pan(self._pan_deg + delta_deg)

    def nudge_tilt(self, delta_dmx: int) -> None:
        """Nudge tilt by delta_dmx DMX steps."""
        self.set_tilt(self._tilt_dmx + delta_dmx)

    def go_to_preset(self, preset_name: str) -> bool:
        """Move to a named position preset.  Returns False if not found."""
        positions = self._load_raw().get("positions", {})
        p = positions.get(preset_name)
        if p is None:
            return False
        self.set_pan(float(p.get("pan_deg",  0.0)))
        self.set_tilt(int(p.get("tilt_dmx",  DEFAULT_TILT)))
        return True

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save_position(self, name: str, description: str = "") -> None:
        """Save current pan/tilt as a named position preset."""
        data = self._load_raw()
        data.setdefault("version", 1)
        data.setdefault("positions", {})
        data["positions"][name] = {
            "pan_deg":     round(self._pan_deg, 2),
            "tilt_dmx":    self._tilt_dmx,
            "description": description,
        }
        with open(self._positions_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def delete_position(self, name: str) -> bool:
        """Remove a named position preset.  Returns True if it existed."""
        data = self._load_raw()
        positions = data.get("positions", {})
        if name not in positions:
            return False
        del positions[name]
        with open(self._positions_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return True

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _flush(self) -> None:
        """Write current pan/tilt to the DMX universe (dimmer on for visibility)."""
        base    = self._fixture.dmx_address - 1
        pan_dmx = pan_degrees_to_dmx(self._pan_deg)
        self._universe.set_channels(base + 1, [
            pan_dmx,         # Ch1 Pan
            0,               # Ch2 Pan Fine
            self._tilt_dmx,  # Ch3 Tilt
            0,               # Ch4 Tilt Fine
            255,             # Ch5 Red   — white for visibility
            255,             # Ch6 Green
            255,             # Ch7 Blue
            _AIM_DIMMER,     # Ch8 Dimmer
            0,               # Ch9 Speed — fastest
            0,               # Ch10 Reset
        ])

    def _load_raw(self) -> dict:
        if not os.path.exists(self._positions_file):
            return {"version": 1, "positions": {}}
        try:
            with open(self._positions_file, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {"version": 1, "positions": {}}
