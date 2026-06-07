"""
Song Preview runner — Sprint 3.

Loads an audio file, runs offline analysis, drives the DeterministicEngine,
and plays back the generated FixtureStateTimeline in the 2D visualizer with
a waveform strip and playback controls.

Run from the repo root:
  python scripts/test_song_preview.py --file song.wav --mode banger
  python scripts/test_song_preview.py --file song.wav --mode dinner --seed 7
  python scripts/test_song_preview.py --simulate                # synthetic audio demo

If no --file is given (or --simulate is passed), a 30-second synthetic audio
signal is generated and analyzed in-memory — no real audio file needed.

Keyboard controls:
  Space       — Play / Pause
  ← / →       — Seek ±5 seconds
  [ / ]       — Seek to previous / next detected phrase
  R           — Re-run preview with current settings (re-generates timeline)
  O/D/B/I/S/L — Switch mode and re-run
  Q / Esc     — Quit
"""

import sys
import os
import argparse
import time
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import pygame
except ImportError:
    print("[ERROR] pygame is required: pip install 'pygame>=2.5.0'")
    sys.exit(1)

from audio.offline_analyzer import OfflineAnalyzer
from audio.synthetic        import SyntheticAudioSource
from engine.settings_snapshot import SettingsSnapshot
from engine.deterministic     import DeterministicEngine
from engine.palettes          import load_all_palettes
from engine.modes             import get_mode, MODES

from app.render.visualizer import Visualizer
from app.render.playback   import PlaybackController
from app.render.waveform   import WaveformDisplay

ROOT         = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PALETTES_DIR = os.path.join(ROOT, "config", "palettes")

# Window dimensions: standard rig area + waveform strip
WINDOW_W  = 1200
WINDOW_H  = 960   # 800 rig + 160 waveform/controls
WAVEFORM_Y      = 800
WAVEFORM_HEIGHT = 130
CONTROLS_Y      = 930


# ---------------------------------------------------------------------------
# Synthetic audio builder (no file needed)
# ---------------------------------------------------------------------------

def _build_synthetic_audio(duration_s: float = 30.0,
                             sample_rate: int = 44100) -> np.ndarray:
    """Generate a float32 mono array using SyntheticAudioSource."""
    src        = SyntheticAudioSource(sample_rate=sample_rate, block_size=1024)
    src.start()
    n_blocks   = int(duration_s * sample_rate / 1024) + 1
    chunks     = []
    for _ in range(n_blocks):
        block = src.get_latest_block()
        if block is not None:
            chunks.append(block.flatten())
    src.stop()
    if chunks:
        audio = np.concatenate(chunks)[: int(duration_s * sample_rate)]
    else:
        audio = np.zeros(int(duration_s * sample_rate), dtype=np.float32)
    return audio.astype(np.float32)


# ---------------------------------------------------------------------------
# Analysis + generation
# ---------------------------------------------------------------------------

def _run_analysis_and_generation(audio: np.ndarray,
                                   sample_rate: int,
                                   mode_key: str,
                                   seed: int,
                                   palettes: dict):
    """Return (AnalysisTimeline, FixtureStateTimeline) for the given audio."""
    analyzer = OfflineAnalyzer()
    print(f"[Preview] Analyzing {len(audio)/sample_rate:.1f}s of audio …")
    t0 = time.monotonic()
    timeline = analyzer.analyze(audio, sample_rate=sample_rate)
    print(f"[Preview] Analysis done in {time.monotonic()-t0:.2f}s "
          f"({len(timeline.frames)} frames"
          f"{', BPM ~' + str(timeline.bpm_estimate) if timeline.bpm_estimate else ''})")

    settings = SettingsSnapshot(
        mode_key=mode_key,
        palette_key=get_mode(mode_key).palette_key,
    )
    engine   = DeterministicEngine(settings, seed=seed)
    print(f"[Preview] Generating {mode_key} lighting (seed={seed}) …")
    t0 = time.monotonic()
    fx_timeline = engine.generate(timeline, palettes)
    print(f"[Preview] Generation done in {time.monotonic()-t0:.2f}s "
          f"({len(fx_timeline.frames)} fixture frames)")
    return timeline, fx_timeline


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="LightBrain Song Preview (Sprint 3)")
    parser.add_argument("--file",     type=str,  default=None,
                        help="Path to audio file (WAV/FLAC). Omit for synthetic demo.")
    parser.add_argument("--simulate", "--demo", dest="simulate",
                        action="store_true",
                        help="Use synthetic audio instead of a real file")
    parser.add_argument("--mode",     type=str,  default="open_dance",
                        help="Starting mode key (default: open_dance)")
    parser.add_argument("--seed",     type=int,  default=42,
                        help="Random seed for deterministic generation (default: 42)")
    parser.add_argument("--duration", type=float, default=30.0,
                        help="Duration of synthetic audio in seconds (default: 30)")
    args = parser.parse_args()

    palettes = load_all_palettes(PALETTES_DIR)
    if not palettes:
        print("[ERROR] No palettes found in config/palettes/")
        sys.exit(1)

    # ---- Load or generate audio ----
    sample_rate = 44100

    if args.file and not args.simulate:
        from audio.file_loader import load_audio_file
        print(f"[Preview] Loading {args.file} …")
        audio, sample_rate = load_audio_file(args.file)
        print(f"[Preview] Loaded {len(audio)/sample_rate:.1f}s at {sample_rate} Hz")
    else:
        print(f"[Preview] Generating {args.duration:.0f}s synthetic audio …")
        audio = _build_synthetic_audio(args.duration, sample_rate)

    current_mode = args.mode if args.mode in MODES else "open_dance"
    current_seed = args.seed

    # ---- Run analysis + generation ----
    analysis_tl, fx_tl = _run_analysis_and_generation(
        audio, sample_rate, current_mode, current_seed, palettes
    )

    # ---- Pygame setup ----
    pygame.init()
    pygame.font.init()
    screen = pygame.display.set_mode((WINDOW_W, WINDOW_H))
    pygame.display.set_caption("LightBrain Song Preview — Sprint 3")
    clock  = pygame.time.Clock()

    visualizer = Visualizer(WINDOW_W, WINDOW_H)
    waveform   = WaveformDisplay(width=WINDOW_W, height=WAVEFORM_HEIGHT)
    waveform.load(analysis_tl)

    controller = PlaybackController()
    controller.load(fx_tl)

    # ---- Keyboard mode map ----
    _MODE_KEYS = {
        pygame.K_o: "open_dance",
        pygame.K_d: "dinner",
        pygame.K_b: "banger",
        pygame.K_i: "indian_latin",
        pygame.K_s: "speech",
        pygame.K_l: "slow_dance",
    }

    # ---- phrase seek helper ----
    phrase_times = sorted(
        e.time_s for e in analysis_tl.events if e.label == "phrase_start"
    )

    def _seek_phrase(direction: int) -> None:
        """Seek to the previous (dir=-1) or next (dir=+1) phrase start."""
        ct = controller.current_time_s
        if direction > 0:
            later = [t for t in phrase_times if t > ct + 0.5]
            if later:
                controller.seek(later[0])
        else:
            earlier = [t for t in phrase_times if t < ct - 0.5]
            if earlier:
                controller.seek(earlier[-1])

    def _rerun(mode_key: str, seed: int) -> None:
        nonlocal analysis_tl, fx_tl, current_mode, current_seed, phrase_times
        current_mode = mode_key
        current_seed = seed
        # Re-analysis is not needed — reuse existing AnalysisTimeline
        _, fx_tl = _run_analysis_and_generation(
            audio, sample_rate, mode_key, seed, palettes
        )
        controller.load(fx_tl)
        phrase_times = sorted(
            e.time_s for e in analysis_tl.events if e.label == "phrase_start"
        )

    try:
        font_small = pygame.font.SysFont("monospace", 12)
    except Exception:
        font_small = None

    running = True
    was_playing_before_rerun = False

    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False

            elif event.type == pygame.KEYDOWN:
                k = event.key

                if k in (pygame.K_q, pygame.K_ESCAPE):
                    running = False

                elif k == pygame.K_SPACE:
                    controller.toggle_play_pause()

                elif k == pygame.K_LEFT:
                    controller.step(-5.0)

                elif k == pygame.K_RIGHT:
                    controller.step(5.0)

                elif k == pygame.K_LEFTBRACKET:
                    _seek_phrase(-1)

                elif k == pygame.K_RIGHTBRACKET:
                    _seek_phrase(1)

                elif k == pygame.K_r:
                    _rerun(current_mode, current_seed)

                elif k in _MODE_KEYS:
                    _rerun(_MODE_KEYS[k], current_seed)

        # ---- Update playback position ----
        rig_state = controller.update()

        # ---- Render rig visualizer ----
        screen.fill((0, 0, 0))
        if rig_state is not None:
            visualizer.draw(screen, rig_state)

        # ---- Render waveform strip ----
        waveform.draw(screen, x=0, y=WAVEFORM_Y,
                      current_time_s=controller.current_time_s)

        # ---- Playback controls bar ----
        _draw_controls(screen, controller, current_mode, current_seed,
                       font_small, CONTROLS_Y)

        pygame.display.flip()
        clock.tick(40)

    pygame.quit()
    print("Song Preview closed.")


def _draw_controls(screen, controller, mode_key, seed, font, y):
    """Draw a minimal playback controls bar."""
    try:
        import pygame
        w = screen.get_width()
        pygame.draw.rect(screen, (20, 20, 35), (0, y, w, 30))

        # Progress bar
        bar_x, bar_y = 120, y + 10
        bar_w, bar_h = w - 260, 10
        pygame.draw.rect(screen, (50, 50, 80), (bar_x, bar_y, bar_w, bar_h))
        fill_w = int(controller.progress * bar_w)
        pygame.draw.rect(screen, (100, 150, 255),
                         (bar_x, bar_y, fill_w, bar_h))

        if font:
            play_sym = "▶" if not controller.is_playing else "⏸"
            label    = (f"{play_sym}  Mode: {mode_key:<12}  Seed: {seed}"
                        f"  [Space]=play/pause  ←→=seek5s  []=phrases  R=rerun  Q=quit")
            surf = font.render(label, True, (160, 160, 200))
            screen.blit(surf, (4, y + 8))
    except Exception:
        pass


if __name__ == "__main__":
    main()
