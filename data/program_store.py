"""
ProgramStore — save and load LightingProgram instances as JSON files.

Each program is stored as one JSON file:
    {store_dir}/{program_id}.json

A lightweight index tracks summaries for fast listing without loading full data:
    {store_dir}/index.json

Usage:
    store = ProgramStore("/path/to/programs/")
    path  = store.save(program)
    prog  = store.load(program_id)
    all_  = store.list_programs()   # List[ProgramSummary], newest first
    store.delete(program_id)
    match = store.find_by_fingerprint(sha256_hex)
"""

import json
import os
import time
from dataclasses import asdict
from typing import List, Optional

from data.lighting_program import LightingProgram, ProgramSummary
from audio.offline_analyzer import AnalysisTimeline, AnalysisFrame, EventMarker
from app.render.fixture_state import (
    FixtureStateTimeline, TimedFrame, RigVisualState,
    UplightState, WashState, BeamState, SparkleState, ImpactState,
)
from engine.settings_snapshot import SettingsSnapshot


class ProgramStore:
    """JSON-backed store for LightingProgram instances."""

    def __init__(self, store_dir: str):
        self._dir = store_dir
        os.makedirs(store_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def save(self, program: LightingProgram) -> str:
        """Serialize and write program to disk. Returns the file path."""
        program.updated_at = time.time()
        data = _serialize_program(program)
        path = os.path.join(self._dir, f"{program.program_id}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, separators=(",", ":"))
        self._update_index(program)
        return path

    def load(self, program_id: str) -> LightingProgram:
        """Load a program by ID. Raises FileNotFoundError if not found."""
        path = os.path.join(self._dir, f"{program_id}.json")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return _deserialize_program(data)

    def delete(self, program_id: str) -> None:
        """Delete a program from disk and index. Silent if not found."""
        path = os.path.join(self._dir, f"{program_id}.json")
        if os.path.exists(path):
            os.remove(path)
        self._remove_from_index(program_id)

    def list_programs(self) -> List[ProgramSummary]:
        """Return all program summaries, sorted newest first."""
        index = self._load_index()
        summaries = []
        for item in index.values():
            try:
                summaries.append(ProgramSummary(**item))
            except (TypeError, KeyError):
                pass
        return sorted(summaries, key=lambda s: s.created_at, reverse=True)

    def find_by_fingerprint(self, fingerprint: str) -> Optional[LightingProgram]:
        """Load the most recently created program matching the given fingerprint."""
        index = self._load_index()
        matches = [
            item for item in index.values()
            if item.get("song_fingerprint") == fingerprint
        ]
        if not matches:
            return None
        best = max(matches, key=lambda m: m.get("created_at", 0.0))
        return self.load(best["program_id"])

    def count(self) -> int:
        """Return the number of saved programs."""
        return len(self._load_index())

    # ------------------------------------------------------------------
    # Index helpers
    # ------------------------------------------------------------------

    def _index_path(self) -> str:
        return os.path.join(self._dir, "index.json")

    def _load_index(self) -> dict:
        path = self._index_path()
        if not os.path.exists(path):
            return {}
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_index(self, index: dict) -> None:
        with open(self._index_path(), "w", encoding="utf-8") as f:
            json.dump(index, f, indent=2)

    def _update_index(self, program: LightingProgram) -> None:
        index = self._load_index()
        index[program.program_id] = {
            "program_id":     program.program_id,
            "name":           program.name,
            "song_file_path": program.song_file_path,
            "song_fingerprint": program.song_fingerprint,
            "song_duration_s":  program.song_duration_s,
            "mode_key":       (program.settings.mode_key if program.settings else ""),
            "created_at":     program.created_at,
            "notes":          program.notes,
        }
        self._save_index(index)

    def _remove_from_index(self, program_id: str) -> None:
        index = self._load_index()
        index.pop(program_id, None)
        self._save_index(index)


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def _serialize_program(program: LightingProgram) -> dict:
    """Convert LightingProgram to a JSON-serializable dict."""
    return {
        "program_id":    program.program_id,
        "name":          program.name,
        "version":       program.version,
        "song_file_path":    program.song_file_path,
        "song_fingerprint":  program.song_fingerprint,
        "song_duration_s":   program.song_duration_s,
        "settings":      asdict(program.settings) if program.settings else None,
        "random_seed":   program.random_seed,
        "analysis_timeline":     _ser_analysis(program.analysis_timeline),
        "fixture_state_timeline": _ser_fx(program.fixture_state_timeline),
        "created_at":    program.created_at,
        "updated_at":    program.updated_at,
        "notes":         program.notes,
    }


def _ser_analysis(tl: Optional[AnalysisTimeline]) -> Optional[dict]:
    if tl is None:
        return None
    return {
        "frames": [asdict(f) for f in tl.frames],
        "events": [asdict(e) for e in tl.events],
        "duration_s":   tl.duration_s,
        "sample_rate":  tl.sample_rate,
        "hop_size":     tl.hop_size,
        "window_size":  tl.window_size,
        "bpm_estimate": tl.bpm_estimate,
        "key_estimate": tl.key_estimate,
    }


def _ser_fx(tl: Optional[FixtureStateTimeline]) -> Optional[dict]:
    if tl is None:
        return None
    return {
        "frames":          [_ser_timed_frame(f) for f in tl.frames],
        "duration_s":      tl.duration_s,
        "sample_rate_fps": tl.sample_rate_fps,
        "random_seed":     tl.random_seed,
        "settings_key":    tl.settings_key,
    }


def _ser_timed_frame(tf: TimedFrame) -> dict:
    return {"time_s": tf.time_s, "state": _ser_rig_state(tf.state)}


def _ser_rig_state(s: RigVisualState) -> dict:
    return {
        "mode":           s.mode,
        "palette_name":   s.palette_name,
        "low_energy":     s.low_energy,
        "mid_energy":     s.mid_energy,
        "high_energy":    s.high_energy,
        "overall_energy": s.overall_energy,
        "room_brightness": s.room_brightness,
        "impact_value":   s.impact_value,
        "uplights":  [_ser_uplight(u)  for u in s.uplights],
        "washes":    [_ser_wash(w)     for w in s.washes],
        "beams":     [_ser_beam(b)     for b in s.beams],
        "sparkles":  [_ser_sparkle(sp) for sp in s.sparkles],
        "impacts":   [_ser_impact(i)   for i in s.impacts],
        "blackout_active": s.blackout_active,
    }


def _ser_uplight(u: UplightState) -> dict:
    return {"fixture_id": u.fixture_id, "x": u.x, "y": u.y,
            "color_rgb": list(u.color_rgb), "brightness": u.brightness,
            "active": u.active}


def _ser_wash(w: WashState) -> dict:
    return {"fixture_id": w.fixture_id, "x": w.x, "y": w.y,
            "color_rgb": list(w.color_rgb), "brightness": w.brightness,
            "radius": w.radius, "pulse_strength": w.pulse_strength,
            "active": w.active}


def _ser_beam(b: BeamState) -> dict:
    return {"fixture_id": b.fixture_id, "x": b.x, "y": b.y,
            "color_rgb": list(b.color_rgb), "brightness": b.brightness,
            "angle_degrees": b.angle_degrees, "length": b.length,
            "spread": b.spread, "movement_speed": b.movement_speed,
            "active": b.active}


def _ser_sparkle(sp: SparkleState) -> dict:
    return {"fixture_id": sp.fixture_id, "x": sp.x, "y": sp.y,
            "color_rgb": list(sp.color_rgb), "brightness": sp.brightness,
            "sparkle_amount": sp.sparkle_amount, "active": sp.active}


def _ser_impact(i: ImpactState) -> dict:
    return {"fixture_id": i.fixture_id, "x": i.x, "y": i.y,
            "brightness": i.brightness, "flash_active": i.flash_active,
            "active": i.active}


# ---------------------------------------------------------------------------
# Deserialization
# ---------------------------------------------------------------------------

def _deserialize_program(data: dict) -> LightingProgram:
    settings = None
    if data.get("settings"):
        settings = SettingsSnapshot(**data["settings"])

    return LightingProgram(
        program_id=    data["program_id"],
        name=          data["name"],
        version=       data.get("version", 1),
        song_file_path=data.get("song_file_path", ""),
        song_fingerprint=data.get("song_fingerprint", ""),
        song_duration_s= data.get("song_duration_s", 0.0),
        settings=      settings,
        random_seed=   data.get("random_seed", 42),
        analysis_timeline=      _deser_analysis(data.get("analysis_timeline")),
        fixture_state_timeline= _deser_fx(data.get("fixture_state_timeline")),
        created_at=    data.get("created_at", 0.0),
        updated_at=    data.get("updated_at", 0.0),
        notes=         data.get("notes", ""),
    )


def _deser_analysis(d: Optional[dict]) -> Optional[AnalysisTimeline]:
    if d is None:
        return None
    return AnalysisTimeline(
        frames=    [AnalysisFrame(**f) for f in d.get("frames", [])],
        events=    [EventMarker(**e)   for e in d.get("events", [])],
        duration_s=  d["duration_s"],
        sample_rate= d["sample_rate"],
        hop_size=    d["hop_size"],
        window_size= d["window_size"],
        bpm_estimate=d.get("bpm_estimate"),
        key_estimate=d.get("key_estimate"),
    )


def _deser_fx(d: Optional[dict]) -> Optional[FixtureStateTimeline]:
    if d is None:
        return None
    frames = [
        TimedFrame(time_s=f["time_s"], state=_deser_rig_state(f["state"]))
        for f in d.get("frames", [])
    ]
    return FixtureStateTimeline(
        frames=          frames,
        duration_s=      d["duration_s"],
        sample_rate_fps= d["sample_rate_fps"],
        random_seed=     d["random_seed"],
        settings_key=    d["settings_key"],
    )


def _deser_rig_state(d: dict) -> RigVisualState:
    return RigVisualState(
        mode=          d["mode"],
        palette_name=  d["palette_name"],
        low_energy=    d["low_energy"],
        mid_energy=    d["mid_energy"],
        high_energy=   d["high_energy"],
        overall_energy=d["overall_energy"],
        room_brightness=d["room_brightness"],
        impact_value=  d["impact_value"],
        uplights= [_deser_uplight(u)  for u in d.get("uplights", [])],
        washes=   [_deser_wash(w)     for w in d.get("washes", [])],
        beams=    [_deser_beam(b)     for b in d.get("beams", [])],
        sparkles= [_deser_sparkle(s)  for s in d.get("sparkles", [])],
        impacts=  [_deser_impact(i)   for i in d.get("impacts", [])],
        blackout_active=d.get("blackout_active", False),
    )


def _deser_uplight(d: dict) -> UplightState:
    return UplightState(
        fixture_id=d["fixture_id"], x=d["x"], y=d["y"],
        color_rgb=tuple(d["color_rgb"]), brightness=d["brightness"],
        active=d.get("active", True),
    )


def _deser_wash(d: dict) -> WashState:
    return WashState(
        fixture_id=d["fixture_id"], x=d["x"], y=d["y"],
        color_rgb=tuple(d["color_rgb"]), brightness=d["brightness"],
        radius=d["radius"], pulse_strength=d["pulse_strength"],
        active=d.get("active", True),
    )


def _deser_beam(d: dict) -> BeamState:
    return BeamState(
        fixture_id=d["fixture_id"], x=d["x"], y=d["y"],
        color_rgb=tuple(d["color_rgb"]), brightness=d["brightness"],
        angle_degrees=d["angle_degrees"], length=d["length"],
        spread=d["spread"], movement_speed=d["movement_speed"],
        active=d.get("active", True),
    )


def _deser_sparkle(d: dict) -> SparkleState:
    return SparkleState(
        fixture_id=d["fixture_id"], x=d["x"], y=d["y"],
        color_rgb=tuple(d["color_rgb"]), brightness=d["brightness"],
        sparkle_amount=d["sparkle_amount"], active=d.get("active", True),
    )


def _deser_impact(d: dict) -> ImpactState:
    return ImpactState(
        fixture_id=d["fixture_id"], x=d["x"], y=d["y"],
        brightness=d["brightness"], flash_active=d["flash_active"],
        active=d.get("active", True),
    )
