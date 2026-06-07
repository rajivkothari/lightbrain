"""
Lane renderers — translate smoothed audio energy + palette state into
per-fixture lighting commands.

Sprint 1: Room Lane only.
Sprint 1B: RoomLane uses per-mode brightness profiles (base/max brightness,
pulse amount, saturation scale) so each mode has its own lighting character.

The Room Lane is the "ambiance" layer:
- Color comes from the active palette blend
- Brightness maps room energy into [base_brightness, max_brightness] range
- A bass-breathing pulse (impact × pulse_amount) adds life to the dimmer
- Saturation is scaled by mode.saturation_scale for softer or punchier looks
"""

from dataclasses import dataclass, field
from typing import Optional, Tuple

from engine.palettes import HSVColor, PaletteBlender, Palette
from engine.safety import SafetyEngine


@dataclass
class RoomLaneOutput:
    """Result of one Room Lane render pass."""
    hsv: HSVColor           # final color before gamma
    brightness: float       # final brightness 0.0–1.0 (after safety)
    strobe: float           # 0.0 (always off Sprint 1)
    raw_room_energy: float
    raw_impact_energy: float
    base_brightness: float = 0.0   # brightness from mode mapping (before pulse)
    pulse_brightness: float = 0.0  # additive bass lift contribution
    # Sprint 2: palette-driven channel levels (0.0–1.0, zeroed on blackout)
    white: float = 0.0
    amber: float = 0.0
    uv: float = 0.0


class RoomLane:
    """
    Room Lane — slow ambient color behavior for uplights and wash fixtures.

    Inputs each frame:
        smoothed_room:   long-tail room energy follower value (0.0–1.0)
        impact:          short-attack impact follower for bass breathing
        safety:          SafetyEngine for final scaling
        master_dimmer:   0.0–1.0 master override
        group_intensity: 0.0–1.0 per-group scale

    Behavior (Sprint 1B):
        - Base brightness maps room energy into mode's [base, max] range
        - Bass pulse = impact × mode.pulse_amount (additive lift)
        - Saturation is scaled by mode.saturation_scale
        - Palette blender uses mode.hold_ms for color hold timing
    """

    # Fallback values used when no mode is set (bare-bones default)
    _FALLBACK_BASE   = 0.0
    _FALLBACK_MAX    = 1.0
    _FALLBACK_PULSE  = 0.15
    _FALLBACK_SAT    = 1.0

    def __init__(self, palette: Palette, mode=None):
        hold_ms          = mode.hold_ms if mode is not None else 0.0
        self._blender    = PaletteBlender(palette, hold_ms=hold_ms)
        self._mode       = mode  # Optional[Mode]

    def set_palette(self, palette: Palette) -> None:
        self._blender.set_palette(palette)

    def set_mode(self, mode) -> None:
        """Update the mode profile and propagate hold_ms to the blender."""
        self._mode = mode
        self._blender.set_hold_ms(mode.hold_ms)

    def render(
        self,
        smoothed_room: float,
        impact: float,
        safety: SafetyEngine,
        master_dimmer: float = 1.0,
        group_intensity: float = 1.0,
        beat_trigger: bool = False,
    ) -> RoomLaneOutput:
        """Render one frame of Room Lane output."""
        mode  = self._mode
        color = self._blender.update(energy=smoothed_room, beat_trigger=beat_trigger)

        if mode is not None:
            base_br = (mode.base_brightness
                       + smoothed_room * (mode.max_brightness - mode.base_brightness))
            pulse   = impact * mode.pulse_amount
            sat     = min(1.0, color.s * mode.saturation_scale)
            # Sprint 2: compute WAU channel levels from mode parameters
            white = min(1.0, mode.white_base
                        + smoothed_room * mode.white_scale
                        + impact * mode.white_impact)
            amber = min(1.0, mode.amber_base + smoothed_room * mode.amber_scale)
            uv    = min(1.0, mode.uv_base    + smoothed_room * mode.uv_scale)
        else:
            base_br = smoothed_room * self._FALLBACK_MAX + self._FALLBACK_BASE
            pulse   = impact * self._FALLBACK_PULSE
            sat     = min(1.0, color.s * self._FALLBACK_SAT)
            white   = amber = uv = 0.0

        raw_brightness = min(1.0, base_br + pulse) * group_intensity

        safe_brightness, safe_strobe = safety.apply(raw_brightness)
        final_brightness = min(1.0, safe_brightness * master_dimmer)

        # Blackout zeros WAU channels too
        if safety.state.blackout_active:
            white = amber = uv = 0.0

        output_color = HSVColor(
            h=color.h,
            s=sat,
            v=final_brightness,
            name=color.name,
        )

        return RoomLaneOutput(
            hsv=output_color,
            brightness=final_brightness,
            strobe=safe_strobe,
            raw_room_energy=smoothed_room,
            raw_impact_energy=impact,
            base_brightness=base_br,
            pulse_brightness=pulse,
            white=white,
            amber=amber,
            uv=uv,
        )

    # ------------------------------------------------------------------
    # Palette blender observables (for the overlay)
    # ------------------------------------------------------------------

    @property
    def palette_name(self) -> str:
        return self._blender.palette_name

    @property
    def current_color_name(self) -> str:
        return self._blender.current_color_name

    @property
    def next_color_name(self) -> str:
        return self._blender.next_color_name

    @property
    def hold_remaining_ms(self) -> float:
        return self._blender.hold_remaining_ms

    @property
    def transition_progress(self) -> float:
        return self._blender.transition_progress
