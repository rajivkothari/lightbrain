"""
LightingProgram — the saved output of a Song Preview deterministic pass.

Stores everything needed to replay or re-run the lighting show for a given
song: the full AnalysisTimeline, generated FixtureStateTimeline, and the
SettingsSnapshot used during generation.

Usage:
    program = LightingProgram.create(
        name="Jungle Night - Banger",
        audio=audio_array,
        sample_rate=44100,
        song_file_path="/path/to/song.wav",
        settings=settings,
        random_seed=42,
        analysis_timeline=analysis_tl,
        fixture_state_timeline=fx_tl,
    )
"""

import hashlib
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from audio.offline_analyzer import AnalysisTimeline
from app.render.fixture_state import FixtureStateTimeline
from engine.settings_snapshot import SettingsSnapshot

PROGRAM_SCHEMA_VERSION = 1


def compute_song_fingerprint(audio: np.ndarray, sample_rate: int) -> str:
    """
    SHA-256 of the first 30 seconds of audio (normalized to int16).

    Volume-normalized before quantizing so the same song at different
    recording levels produces the same fingerprint.
    """
    if audio is None or len(audio) == 0:
        return hashlib.sha256(b"").hexdigest()
    max_samples = int(30 * sample_rate)
    chunk = np.asarray(audio[:max_samples], dtype=np.float32).flatten()
    peak = float(np.max(np.abs(chunk))) if len(chunk) > 0 else 0.0
    if peak > 1e-6:
        chunk = chunk / peak
    pcm = (chunk * 32767).astype(np.int16)
    return hashlib.sha256(pcm.tobytes()).hexdigest()


@dataclass
class ProgramSummary:
    """Lightweight summary of a LightingProgram for library listing."""
    program_id: str
    name: str
    song_file_path: str
    song_fingerprint: str
    song_duration_s: float
    mode_key: str
    created_at: float
    notes: str = ""


@dataclass
class LightingProgram:
    """
    Complete saved lighting show for one song.

    Contains everything to replay the show exactly (fixture_state_timeline)
    or to re-run the deterministic engine with new settings (analysis_timeline
    + settings + random_seed).
    """
    # Identity
    program_id: str
    name: str
    version: int = PROGRAM_SCHEMA_VERSION

    # Source
    song_file_path: str = ""
    song_fingerprint: str = ""
    song_duration_s: float = 0.0

    # Generation parameters
    settings: Optional[SettingsSnapshot] = None
    random_seed: int = 42

    # Analysis + generated data
    analysis_timeline: Optional[AnalysisTimeline] = None
    fixture_state_timeline: Optional[FixtureStateTimeline] = None

    # Metadata
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    notes: str = ""

    @staticmethod
    def create(
        name: str,
        audio: np.ndarray,
        sample_rate: int,
        song_file_path: str,
        settings: SettingsSnapshot,
        random_seed: int,
        analysis_timeline: AnalysisTimeline,
        fixture_state_timeline: FixtureStateTimeline,
        notes: str = "",
    ) -> "LightingProgram":
        """Factory that auto-generates program_id, fingerprint, and timestamps."""
        now = time.time()
        return LightingProgram(
            program_id=str(uuid.uuid4()),
            name=name,
            song_file_path=song_file_path,
            song_fingerprint=compute_song_fingerprint(audio, sample_rate),
            song_duration_s=analysis_timeline.duration_s,
            settings=settings,
            random_seed=random_seed,
            analysis_timeline=analysis_timeline,
            fixture_state_timeline=fixture_state_timeline,
            created_at=now,
            updated_at=now,
            notes=notes,
        )

    def to_summary(self) -> ProgramSummary:
        """Return a lightweight summary of this program."""
        mode_key = self.settings.mode_key if self.settings else ""
        return ProgramSummary(
            program_id=self.program_id,
            name=self.name,
            song_file_path=self.song_file_path,
            song_fingerprint=self.song_fingerprint,
            song_duration_s=self.song_duration_s,
            mode_key=mode_key,
            created_at=self.created_at,
            notes=self.notes,
        )
