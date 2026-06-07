"""
WaveformDisplay — pygame waveform and energy lane renderer for Song Preview Mode.

Renders a static background surface from AnalysisTimeline data (waveform,
energy lanes, onset markers, event labels) and draws the playhead on top
each frame. This is split into static (built once) and dynamic (drawn every
frame) parts so redrawing is cheap.

Usage:
    display = WaveformDisplay(width=1200, height=150)
    display.load(timeline)   # build static surface once

    # Each frame:
    display.draw(screen, x=0, y=800, current_time_s=t)
"""

from typing import Optional

try:
    import pygame
    _PYGAME_AVAILABLE = True
except ImportError:
    _PYGAME_AVAILABLE = False

from audio.offline_analyzer import AnalysisTimeline


# ---------------------------------------------------------------------------
# Colors
# ---------------------------------------------------------------------------

_BG           = (12,  12,  20)
_WAVEFORM_COL = (80,  80,  120)
_LOW_COL      = (220, 60,  60)
_MID_COL      = (60,  180, 60)
_HIGH_COL     = (60,  120, 220)
_ENERGY_COL   = (200, 200, 60)
_ONSET_COL    = (255, 255, 255, 160)
_DROP_COL     = (255, 80,  80,  200)
_PHRASE_COL   = (80,  255, 80,  150)
_PLAYHEAD_COL = (255, 255, 255)
_GRID_COL     = (30,  30,  50)
_TEXT_COL     = (160, 160, 200)


class WaveformDisplay:
    """
    Pygame-based waveform + energy lane display for Song Preview.

    The static surface is generated once per loaded timeline (build_surface).
    The dynamic playhead is drawn on top each render call.
    """

    # Lane layout (proportional rows within the height)
    _ROW_LABELS  = ["LOW", "MID", "HIGH", "ENERGY"]
    _ROW_COLORS  = [_LOW_COL, _MID_COL, _HIGH_COL, _ENERGY_COL]
    _ROW_KEYS    = ["low_energy", "mid_energy", "high_energy", "overall_energy"]

    def __init__(self, width: int = 1200, height: int = 150):
        self._width   = width
        self._height  = height
        self._surface: Optional["pygame.Surface"] = None
        self._timeline: Optional[AnalysisTimeline] = None
        self._duration = 0.0

    def load(self, timeline: AnalysisTimeline) -> None:
        """Pre-render the static surface from the AnalysisTimeline."""
        self._timeline = timeline
        self._duration = timeline.duration_s
        if _PYGAME_AVAILABLE and timeline.frames:
            self._surface = self._build_surface(timeline)
        else:
            self._surface = None

    def draw(
        self,
        screen: "pygame.Surface",
        x: int,
        y: int,
        current_time_s: float,
    ) -> None:
        """
        Blit the static surface and draw the playhead.
        Safe to call even if pygame or timeline is unavailable.
        """
        if not _PYGAME_AVAILABLE or self._surface is None:
            return

        screen.blit(self._surface, (x, y))

        # Playhead
        if self._duration > 0:
            px = x + int((current_time_s / self._duration) * self._width)
            px = max(x, min(px, x + self._width - 1))
            pygame.draw.line(screen, _PLAYHEAD_COL,
                             (px, y), (px, y + self._height - 1), 2)

        # Time label
        self._draw_time_label(screen, x, y, current_time_s)

    # ------------------------------------------------------------------
    # Internal: build static surface
    # ------------------------------------------------------------------

    def _build_surface(self, timeline: AnalysisTimeline) -> "pygame.Surface":
        surface = pygame.Surface((self._width, self._height))
        surface.fill(_BG)

        frames  = timeline.frames
        n       = len(frames)
        w       = self._width
        h       = self._height
        n_lanes = len(self._ROW_KEYS)
        lane_h  = h // (n_lanes + 1)

        # -- Grid lines every 10s --
        if timeline.duration_s > 0:
            step = 10.0
            t = step
            while t < timeline.duration_s:
                gx = int((t / timeline.duration_s) * w)
                pygame.draw.line(surface, _GRID_COL, (gx, 0), (gx, h))
                t += step

        # -- Energy lanes --
        for row_idx, (key, color) in enumerate(
                zip(self._ROW_KEYS, self._ROW_COLORS)):
            row_y_top    = row_idx * lane_h
            row_y_bottom = row_y_top + lane_h - 2
            row_height   = row_y_bottom - row_y_top

            values = [getattr(f, key, 0.0) for f in frames]
            pts = []
            for i, v in enumerate(values):
                px = int(i * w / max(n - 1, 1))
                py = row_y_bottom - int(v * row_height)
                pts.append((px, py))

            if len(pts) >= 2:
                pygame.draw.lines(surface, color, False, pts, 1)

        # -- Onset markers (thin vertical lines on bottom row) --
        onset_y_top = n_lanes * lane_h
        onset_y_bot = onset_y_top + lane_h - 2
        onset_surf  = pygame.Surface((w, onset_y_bot - onset_y_top + 1), pygame.SRCALPHA)

        for frame in frames:
            if frame.is_onset and timeline.duration_s > 0:
                ox = int((frame.time_s / timeline.duration_s) * w)
                alpha = int(80 + frame.onset_strength * 120)
                pygame.draw.line(onset_surf, (*_ONSET_COL[:3], alpha),
                                 (ox, 0), (ox, onset_surf.get_height() - 1), 1)
        surface.blit(onset_surf, (0, onset_y_top))

        # -- Event labels (DROP, phrase_start) --
        try:
            font = pygame.font.SysFont("monospace", 9)
        except Exception:
            font = None

        for event in timeline.events:
            if timeline.duration_s > 0:
                ex = int((event.time_s / timeline.duration_s) * w)
                col = _DROP_COL[:3] if event.label == "drop" else _PHRASE_COL[:3]
                pygame.draw.line(surface, col, (ex, 0), (ex, h - 1), 1)
                if font:
                    label_surf = font.render(event.label[:6].upper(), True, col)
                    surface.blit(label_surf, (ex + 2, 2))

        return surface

    def _draw_time_label(
        self,
        screen: "pygame.Surface",
        x: int,
        y: int,
        current_time_s: float,
    ) -> None:
        if not _PYGAME_AVAILABLE:
            return
        try:
            font  = pygame.font.SysFont("monospace", 11)
            cur_s = int(current_time_s)
            dur_s = int(self._duration)
            label = f"{cur_s // 60:02d}:{cur_s % 60:02d} / {dur_s // 60:02d}:{dur_s % 60:02d}"
            surf  = font.render(label, True, _TEXT_COL)
            screen.blit(surf, (x + self._width - surf.get_width() - 6,
                               y + self._height - surf.get_height() - 2))
        except Exception:
            pass
