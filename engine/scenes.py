"""
Scene Preset Manager — Sprint 7.

Loads named scene presets from config/scenes/*.json and fixture state/position
presets from fixtures/states.json and fixtures/positions.json.

A scene is a complete look: base_mode + per-group overrides for colour, position,
movement, and audio-reactivity.  Scenes sit at priority layer 3 in the renderer
chain:

    blackout → safety → scene overrides → MIDI momentary → MIDI continuous →
    mode engine → audio reactivity
"""

import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from app.render.fixture_state import (
    RigVisualState,
    UplightState,
    BeamState,
    WashState,
    SparkleState,
)

RGB = Tuple[int, int, int]


@dataclass
class PositionPreset:
    """Named pan/tilt position for a moving-head fixture."""
    name:        str
    pan_deg:     float
    tilt_dmx:    int
    description: str = ""


@dataclass
class StatePreset:
    """Named colour/intensity state for any fixture group."""
    name:        str
    rgb:         RGB
    brightness:  float
    strobe_rate: float = 0.0


@dataclass
class GroupOverride:
    """
    Per-fixture-type override within a scene.

    fixture_type  — "uplight", "beam", "wash", or "sparkle"
    groups        — ["all"] or a list of group names
    position_preset — name from positions.json (beams only)
    state_preset  — name from states.json
    movement_mode — "locked" | "constrained" | "inherit"
    audio_reactive — if False, brightness is frozen at state_preset level
    """
    fixture_type:    str
    groups:          List[str]
    position_preset: str  = ""
    state_preset:    str  = ""
    movement_mode:   str  = "inherit"
    audio_reactive:  bool = True


@dataclass
class ScenePreset:
    """
    Complete look definition: a base mode + list of per-group overrides.

    F1-F9 in the UI activates scenes 1-9 in list_scenes() order.
    F10 releases the active scene (returns to pure mode engine output).
    """
    scene_id:    str
    name:        str
    base_mode:   str
    description: str              = ""
    groups:      List[GroupOverride] = field(default_factory=list)


class SceneManager:
    """
    Manages scene, state, and position presets.

    Usage:
        mgr = SceneManager(scenes_dir, positions_file, states_file)
        mgr.load_all()
        mgr.activate_scene("slow_dance")
        overridden = mgr.apply_to_rig_state(live_state)
    """

    def __init__(
        self,
        scenes_dir:     str,
        positions_file: str,
        states_file:    str,
    ) -> None:
        self._scenes_dir     = scenes_dir
        self._positions_file = positions_file
        self._states_file    = states_file

        self._scenes:    Dict[str, ScenePreset]    = {}
        self._positions: Dict[str, PositionPreset] = {}
        self._states:    Dict[str, StatePreset]    = {}
        self._active_id: Optional[str]             = None

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load_all(self) -> None:
        """Load all presets from disk.  Safe to call multiple times (reloads)."""
        self._load_positions()
        self._load_states()
        self._load_scenes()

    def _load_positions(self) -> None:
        if not os.path.exists(self._positions_file):
            return
        with open(self._positions_file, encoding="utf-8") as f:
            data = json.load(f)
        self._positions = {}
        for name, p in data.get("positions", {}).items():
            self._positions[name] = PositionPreset(
                name=        name,
                pan_deg=     float(p.get("pan_deg",  0.0)),
                tilt_dmx=    int(p.get("tilt_dmx",   110)),
                description= p.get("description",    ""),
            )

    def _load_states(self) -> None:
        if not os.path.exists(self._states_file):
            return
        with open(self._states_file, encoding="utf-8") as f:
            data = json.load(f)
        self._states = {}
        for name, s in data.get("states", {}).items():
            raw = s.get("rgb", [255, 255, 255])
            self._states[name] = StatePreset(
                name=        name,
                rgb=         (int(raw[0]), int(raw[1]), int(raw[2])),
                brightness=  float(s.get("brightness",  1.0)),
                strobe_rate= float(s.get("strobe_rate", 0.0)),
            )

    def _load_scenes(self) -> None:
        if not os.path.exists(self._scenes_dir):
            return
        self._scenes = {}
        for fname in sorted(os.listdir(self._scenes_dir)):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(self._scenes_dir, fname)
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                scene = _scene_from_dict(data)
                self._scenes[scene.scene_id] = scene
            except (json.JSONDecodeError, KeyError, OSError) as e:
                import logging
                logging.getLogger(__name__).warning("Failed to load scene %s: %s", fname, e)

    # ------------------------------------------------------------------
    # Activation
    # ------------------------------------------------------------------

    def activate_scene(self, scene_id: str) -> bool:
        """Activate a scene by ID.  Returns True if found, False otherwise."""
        if scene_id in self._scenes:
            self._active_id = scene_id
            return True
        return False

    def release_scene(self) -> None:
        """Release active scene — return to pure mode engine output."""
        self._active_id = None

    @property
    def active_scene(self) -> Optional[ScenePreset]:
        if self._active_id is None:
            return None
        return self._scenes.get(self._active_id)

    @property
    def active_scene_id(self) -> Optional[str]:
        return self._active_id

    @property
    def active_base_mode(self) -> Optional[str]:
        scene = self.active_scene
        return scene.base_mode if scene else None

    # ------------------------------------------------------------------
    # Apply overrides
    # ------------------------------------------------------------------

    def apply_to_rig_state(self, state: RigVisualState) -> RigVisualState:
        """
        Apply active scene overrides to a RigVisualState.

        Returns the input state unchanged when no scene is active.
        Otherwise returns a new RigVisualState with all overrides applied.
        """
        scene = self.active_scene
        if scene is None:
            return state

        new_uplights = [_copy_uplight(u) for u in state.uplights]
        new_beams    = [_copy_beam(b)    for b in state.beams]
        new_washes   = [_copy_wash(w)    for w in state.washes]
        new_sparkles = [_copy_sparkle(s) for s in state.sparkles]

        for ov in scene.groups:
            ft = ov.fixture_type.lower()
            if ft == "uplight":
                for u in new_uplights:
                    self._apply_uplight(u, ov)
            elif ft == "beam":
                for b in new_beams:
                    self._apply_beam(b, ov)
            elif ft == "wash":
                for w in new_washes:
                    self._apply_wash(w, ov)
            elif ft == "sparkle":
                for s in new_sparkles:
                    self._apply_sparkle(s, ov)

        return RigVisualState(
            mode=            scene.base_mode,
            palette_name=    state.palette_name,
            low_energy=      state.low_energy,
            mid_energy=      state.mid_energy,
            high_energy=     state.high_energy,
            overall_energy=  state.overall_energy,
            room_brightness= state.room_brightness,
            impact_value=    state.impact_value,
            uplights=        new_uplights,
            washes=          new_washes,
            beams=           new_beams,
            sparkles=        new_sparkles,
            impacts=         list(state.impacts),
            blackout_active= state.blackout_active,
        )

    def _apply_uplight(self, u: UplightState, ov: GroupOverride) -> None:
        if ov.state_preset and ov.state_preset in self._states:
            sp = self._states[ov.state_preset]
            u.color_rgb = sp.rgb
            if not ov.audio_reactive:
                u.brightness = sp.brightness

    def _apply_beam(self, b: BeamState, ov: GroupOverride) -> None:
        if ov.position_preset and ov.position_preset in self._positions:
            pp = self._positions[ov.position_preset]
            if ov.movement_mode == "locked":
                b.angle_degrees  = pp.pan_deg
                b.movement_speed = 0.0
            elif ov.movement_mode == "constrained":
                b.angle_degrees = max(-30.0, min(30.0, b.angle_degrees))
        if ov.state_preset and ov.state_preset in self._states:
            sp = self._states[ov.state_preset]
            b.color_rgb = sp.rgb
            if not ov.audio_reactive:
                b.brightness = sp.brightness

    def _apply_wash(self, w: WashState, ov: GroupOverride) -> None:
        if ov.state_preset and ov.state_preset in self._states:
            sp = self._states[ov.state_preset]
            w.color_rgb = sp.rgb
            if not ov.audio_reactive:
                w.brightness = sp.brightness

    def _apply_sparkle(self, s: SparkleState, ov: GroupOverride) -> None:
        if ov.state_preset and ov.state_preset in self._states:
            sp = self._states[ov.state_preset]
            s.color_rgb = sp.rgb
            if not ov.audio_reactive:
                s.brightness = sp.brightness

    # ------------------------------------------------------------------
    # Uplight colour helper (for app/main.py RockWedge path)
    # ------------------------------------------------------------------

    def get_uplight_color_override(self) -> Optional[Tuple[RGB, float, bool]]:
        """
        Return (rgb, brightness, audio_reactive) for the active scene's first
        uplight override, or None if no scene or no uplight override.

        Used by app/main.py to apply colour overrides on the HSV render path.
        """
        scene = self.active_scene
        if scene is None:
            return None
        for ov in scene.groups:
            if ov.fixture_type.lower() == "uplight" and ov.state_preset:
                sp = self._states.get(ov.state_preset)
                if sp is not None:
                    return (sp.rgb, sp.brightness, ov.audio_reactive)
        return None

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def list_scenes(self) -> List[ScenePreset]:
        """Return all loaded scenes sorted by scene_id (stable order for F-keys)."""
        return sorted(self._scenes.values(), key=lambda s: s.scene_id)

    def get_scene(self, scene_id: str) -> Optional[ScenePreset]:
        return self._scenes.get(scene_id)

    def get_scene_name(self, scene_id: str) -> str:
        """Return display name for scene_id, falling back to the id itself."""
        scene = self._scenes.get(scene_id)
        return scene.name if scene else scene_id

    def get_state(self, name: str) -> Optional[StatePreset]:
        return self._states.get(name)

    def get_position(self, name: str) -> Optional[PositionPreset]:
        return self._positions.get(name)

    def scene_count(self) -> int:
        return len(self._scenes)

    def state_count(self) -> int:
        return len(self._states)

    def position_count(self) -> int:
        return len(self._positions)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _scene_from_dict(data: dict) -> ScenePreset:
    groups = [
        GroupOverride(
            fixture_type=   g.get("fixture_type",    "uplight"),
            groups=         g.get("groups",          ["all"]),
            position_preset=g.get("position_preset", ""),
            state_preset=   g.get("state_preset",    ""),
            movement_mode=  g.get("movement_mode",   "inherit"),
            audio_reactive= bool(g.get("audio_reactive", True)),
        )
        for g in data.get("groups", [])
    ]
    return ScenePreset(
        scene_id=   data["scene_id"],
        name=       data.get("name",        data["scene_id"]),
        base_mode=  data.get("base_mode",   "open_dance"),
        description=data.get("description", ""),
        groups=     groups,
    )


def _copy_uplight(u: UplightState) -> UplightState:
    return UplightState(
        fixture_id=u.fixture_id, x=u.x, y=u.y,
        color_rgb=u.color_rgb, brightness=u.brightness, active=u.active,
    )


def _copy_beam(b: BeamState) -> BeamState:
    return BeamState(
        fixture_id=b.fixture_id, x=b.x, y=b.y,
        color_rgb=b.color_rgb, brightness=b.brightness,
        angle_degrees=b.angle_degrees, length=b.length, spread=b.spread,
        movement_speed=b.movement_speed, active=b.active,
    )


def _copy_wash(w: WashState) -> WashState:
    return WashState(
        fixture_id=w.fixture_id, x=w.x, y=w.y,
        color_rgb=w.color_rgb, brightness=w.brightness,
        radius=w.radius, pulse_strength=w.pulse_strength, active=w.active,
    )


def _copy_sparkle(s: SparkleState) -> SparkleState:
    return SparkleState(
        fixture_id=s.fixture_id, x=s.x, y=s.y,
        color_rgb=s.color_rgb, brightness=s.brightness,
        sparkle_amount=s.sparkle_amount, active=s.active,
    )
