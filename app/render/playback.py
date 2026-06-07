"""
PlaybackController — real-time playback of a FixtureStateTimeline.

Drives the visualizer during Song Preview Mode by advancing a playhead
through the pre-generated FixtureStateTimeline at real wall-clock speed.

Usage:
    controller = PlaybackController()
    controller.load(timeline)
    controller.play()

    # In the render loop:
    state = controller.update()   # returns RigVisualState | None
    if state:
        visualizer.draw(screen, state)
"""

import time
from typing import Optional

from app.render.fixture_state import FixtureStateTimeline, RigVisualState


class PlaybackController:
    """
    Wall-clock playback of a FixtureStateTimeline.

    update() must be called each frame; it advances the playhead by the
    real elapsed time and returns the nearest frame's RigVisualState.
    When the end is reached, playback stops automatically.
    """

    def __init__(self):
        self._timeline:      Optional[FixtureStateTimeline] = None
        self._current_time:  float = 0.0
        self._playing:       bool  = False
        self._last_wall_time: float = 0.0

    # ------------------------------------------------------------------
    # Load / control
    # ------------------------------------------------------------------

    def load(self, timeline: FixtureStateTimeline) -> None:
        """Load a new timeline. Resets position to 0.0 and enters paused state."""
        self._timeline     = timeline
        self._current_time = 0.0
        self._playing      = False

    def play(self) -> None:
        """Start or resume playback."""
        if self._timeline is None:
            return
        self._playing       = True
        self._last_wall_time = time.monotonic()

    def pause(self) -> None:
        """Pause playback (position is preserved)."""
        self._playing = False

    def toggle_play_pause(self) -> None:
        """Toggle between play and pause."""
        if self._playing:
            self.pause()
        else:
            self.play()

    def seek(self, time_s: float) -> None:
        """Jump to a specific position in the timeline (clamped to duration)."""
        if self._timeline is None:
            return
        self._current_time   = max(0.0, min(time_s, self._timeline.duration_s))
        self._last_wall_time = time.monotonic()

    def step(self, dt_s: float) -> None:
        """Seek relative to the current position."""
        self.seek(self._current_time + dt_s)

    def rewind(self) -> None:
        """Jump to the beginning."""
        self.seek(0.0)

    # ------------------------------------------------------------------
    # Update (call each frame)
    # ------------------------------------------------------------------

    def update(self) -> Optional[RigVisualState]:
        """
        Advance playhead by real elapsed time and return current RigVisualState.
        Returns None if no timeline is loaded.
        """
        if self._timeline is None:
            return None

        if self._playing:
            now   = time.monotonic()
            delta = now - self._last_wall_time
            self._last_wall_time = now

            self._current_time += delta
            if self._current_time >= self._timeline.duration_s:
                self._current_time = self._timeline.duration_s
                self._playing      = False

        return self._timeline.frame_at(self._current_time)

    # ------------------------------------------------------------------
    # State accessors
    # ------------------------------------------------------------------

    @property
    def current_time_s(self) -> float:
        return self._current_time

    @property
    def duration_s(self) -> float:
        return self._timeline.duration_s if self._timeline is not None else 0.0

    @property
    def is_playing(self) -> bool:
        return self._playing

    @property
    def is_loaded(self) -> bool:
        return self._timeline is not None

    @property
    def progress(self) -> float:
        """Playback progress 0.0–1.0."""
        dur = self.duration_s
        return (self._current_time / dur) if dur > 0 else 0.0
