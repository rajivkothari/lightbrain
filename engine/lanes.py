"""
Lane renderers — translate smoothed audio energy + palette state into
per-fixture lighting commands.

Sprint 1: Room Lane only.

The Room Lane is the "ambiance" layer:
- Color comes from the active palette blend
- Brightness tracks smoothed room energy (slow, long-tail)
- A subtle bass-breathing pulse adds life to the dimmer
- Hue changes slowly via palette blend — never every frame
"""

from dataclasses import dataclass
from typing import Tuple

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


class RoomLane:
    """
    Room Lane — slow ambient color behavior for uplights and wash fixtures.

    Inputs each frame:
        smoothed_room:   long-tail room energy follower value
        impact:          short-attack impact follower for bass breathing
        palette:         active Palette object
        safety:          SafetyEngine for final scaling
        master_dimmer:   0.0–1.0 master override
        group_intensity: 0.0–1.0 per-group scale (for future group support)

    Behavior:
        - Base brightness = smoothed_room energy
        - Bass pulse adds a small multiplicative boost on transients
          (impact * BASS_BREATH_DEPTH), keeping the room feeling alive
        - Palette blender drives slow hue changes independent of beat
    """

    # How much the bass transient breathes the dimmer up
    BASS_BREATH_DEPTH = 0.15  # 15% max lift from a full impact hit

    def __init__(self, palette: Palette):
        self._blender = PaletteBlender(palette)

    def set_palette(self, palette: Palette) -> None:
        self._blender.set_palette(palette)

    def render(
        self,
        smoothed_room: float,
        impact: float,
        safety: SafetyEngine,
        master_dimmer: float = 1.0,
        group_intensity: float = 1.0,
    ) -> RoomLaneOutput:
        """
        Render one frame of Room Lane output.

        Returns RoomLaneOutput with color, brightness, and strobe values.
        """
        # Get current blended palette color (advances the blend)
        color = self._blender.update(energy=smoothed_room)

        # Base brightness follows room energy
        base_brightness = smoothed_room

        # Bass breathing: small additive boost from impact transients
        bass_lift = impact * self.BASS_BREATH_DEPTH
        raw_brightness = min(1.0, base_brightness + bass_lift)

        # Apply group intensity scale
        raw_brightness *= group_intensity

        # Safety engine applies blackout, mode scale, master dimmer
        safe_brightness, safe_strobe = safety.apply(raw_brightness)

        # Master dimmer applied on top (safety.apply uses its own internal
        # master but we also allow an explicit per-call override here)
        final_brightness = min(1.0, safe_brightness * master_dimmer)

        # Modulate value channel of HSV with final brightness
        output_color = HSVColor(
            h=color.h,
            s=color.s,
            v=final_brightness,
            name=color.name,
        )

        return RoomLaneOutput(
            hsv=output_color,
            brightness=final_brightness,
            strobe=safe_strobe,
            raw_room_energy=smoothed_room,
            raw_impact_energy=impact,
        )

    @property
    def palette_name(self) -> str:
        return self._blender.palette_name
