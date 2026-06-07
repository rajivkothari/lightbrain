"""
Song Preview runner — Sprint 4.

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
  O/D/B/I/S/L — Switch mode and re-run  (S = slow_dance, L = no longer load!)
  Ctrl+S      — Save current preview as a Lighting Program
  Ctrl+L      — Open program library (load a saved program)
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

from data.lighting_program import LightingProgram, compute_song_fingerprint
from data.program_store    import ProgramStore
from data.setlist_store    import SetlistStore as _SetlistStore

ROOT          = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PALETTES_DIR  = os.path.join(ROOT, "config", "palettes")
PROGRAMS_DIR  = os.path.join(ROOT, "data", "programs")
SETLISTS_DIR  = os.path.join(ROOT, "data", "setlists")

# Window dimensions: standard rig area + waveform strip
WINDOW_W        = 1200
WINDOW_H        = 960   # 800 rig + 160 waveform/controls
WAVEFORM_Y      = 800
WAVEFORM_HEIGHT = 130
CONTROLS_Y      = 930


# ---------------------------------------------------------------------------
# Synthetic audio builder (no file needed)
# ---------------------------------------------------------------------------

def _build_synthetic_audio(duration_s: float = 30.0,
                             sample_rate: int = 44100) -> np.ndarray:
    """Generate a float32 mono array using SyntheticAudioSource."""
    src      = SyntheticAudioSource(sample_rate=sample_rate, block_size=1024)
    src.start()
    n_blocks = int(duration_s * sample_rate / 1024) + 1
    chunks   = []
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
    """Return (AnalysisTimeline, FixtureStateTimeline, SettingsSnapshot)."""
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
    engine = DeterministicEngine(settings, seed=seed)
    print(f"[Preview] Generating {mode_key} lighting (seed={seed}) …")
    t0 = time.monotonic()
    fx_timeline = engine.generate(timeline, palettes)
    print(f"[Preview] Generation done in {time.monotonic()-t0:.2f}s "
          f"({len(fx_timeline.frames)} fixture frames)")
    return timeline, fx_timeline, settings


# ---------------------------------------------------------------------------
# Program save / load helpers
# ---------------------------------------------------------------------------

def _save_program(store: ProgramStore,
                  audio: np.ndarray,
                  sample_rate: int,
                  song_file_path: str,
                  analysis_tl,
                  fx_tl,
                  settings: SettingsSnapshot,
                  seed: int) -> str:
    """Save current preview as a LightingProgram. Returns the program_id."""
    name = f"{settings.mode_key}  {time.strftime('%Y-%m-%d %H:%M')}"
    program = LightingProgram.create(
        name=name,
        audio=audio,
        sample_rate=sample_rate,
        song_file_path=song_file_path,
        settings=settings,
        random_seed=seed,
        analysis_timeline=analysis_tl,
        fixture_state_timeline=fx_tl,
    )
    path = store.save(program)
    print(f"[Preview] Saved: '{name}' → {path}")
    return program.program_id


def _try_reload_audio(song_file_path: str):
    """Try to reload audio from song_file_path. Returns (audio, sr) or None."""
    if not song_file_path or not os.path.exists(song_file_path):
        return None
    try:
        from audio.file_loader import load_audio_file
        audio, sr = load_audio_file(song_file_path)
        return audio, sr
    except Exception as e:
        print(f"[Preview] Could not reload audio from {song_file_path}: {e}")
        return None


# ---------------------------------------------------------------------------
# Program library UI (blocking pygame modal)
# ---------------------------------------------------------------------------

_LIB_BG        = (10,  10,  20)
_LIB_BORDER    = (60,  60,  100)
_LIB_SELECTED  = (40,  60,  110)
_LIB_TEXT      = (200, 200, 220)
_LIB_DIM_TEXT  = (100, 100, 140)
_LIB_HEADER    = (120, 180, 255)
_LIB_DELETE    = (200, 60,  60)


def _show_program_library(screen: "pygame.Surface",
                           store: ProgramStore,
                           font) -> "Optional[LightingProgram]":
    """
    Blocking modal that displays saved programs and lets the user pick one.

    Returns a loaded LightingProgram if the user selects one, or None if
    they cancel (Esc).  Delete key removes the highlighted program.
    """
    programs   = store.list_programs()
    selected   = 0
    w, h       = screen.get_size()
    modal_x    = w // 8
    modal_y    = h // 8
    modal_w    = w - 2 * modal_x
    modal_h    = h - 2 * modal_y
    row_h      = 40
    visible    = (modal_h - 100) // row_h
    scroll_off = 0
    feedback   = ""
    feedback_t = 0.0

    try:
        title_font = pygame.font.SysFont("monospace", 15)
        item_font  = pygame.font.SysFont("monospace", 13)
    except Exception:
        title_font = item_font = font

    clock = pygame.time.Clock()

    while True:
        # -- Events --
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return None

            elif event.type == pygame.KEYDOWN:
                if event.key in (pygame.K_ESCAPE, pygame.K_q):
                    return None

                elif event.key == pygame.K_UP and programs:
                    selected = max(0, selected - 1)
                    if selected < scroll_off:
                        scroll_off = selected

                elif event.key == pygame.K_DOWN and programs:
                    selected = min(len(programs) - 1, selected + 1)
                    if selected >= scroll_off + visible:
                        scroll_off = selected - visible + 1

                elif event.key == pygame.K_RETURN and programs:
                    prog_id = programs[selected].program_id
                    try:
                        return store.load(prog_id)
                    except Exception as e:
                        feedback = f"Load failed: {e}"
                        feedback_t = time.monotonic()

                elif event.key == pygame.K_DELETE and programs:
                    prog_id = programs[selected].program_id
                    store.delete(prog_id)
                    programs = store.list_programs()
                    selected = min(selected, max(0, len(programs) - 1))
                    scroll_off = max(0, scroll_off)
                    feedback = "Deleted."
                    feedback_t = time.monotonic()

        # -- Draw modal --
        overlay = pygame.Surface((w, h), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 180))
        screen.blit(overlay, (0, 0))

        pygame.draw.rect(screen, _LIB_BG,     (modal_x, modal_y, modal_w, modal_h))
        pygame.draw.rect(screen, _LIB_BORDER, (modal_x, modal_y, modal_w, modal_h), 2)

        # Header
        if title_font:
            header = title_font.render(
                "PROGRAM LIBRARY   ↑↓ navigate   Enter=load   Del=delete   Esc=cancel",
                True, _LIB_HEADER,
            )
            screen.blit(header, (modal_x + 12, modal_y + 10))

        pygame.draw.line(screen, _LIB_BORDER,
                         (modal_x + 8,          modal_y + 38),
                         (modal_x + modal_w - 8, modal_y + 38), 1)

        if not programs:
            if item_font:
                msg = item_font.render("No saved programs.", True, _LIB_DIM_TEXT)
                screen.blit(msg, (modal_x + 20, modal_y + 55))
        else:
            for slot, idx in enumerate(range(scroll_off,
                                              min(scroll_off + visible, len(programs)))):
                prog = programs[idx]
                ry   = modal_y + 46 + slot * row_h
                is_sel = idx == selected

                if is_sel:
                    pygame.draw.rect(screen, _LIB_SELECTED,
                                     (modal_x + 4, ry, modal_w - 8, row_h - 2))

                if item_font:
                    dur  = prog.song_duration_s
                    mins = int(dur) // 60
                    secs = int(dur) % 60
                    date = time.strftime("%Y-%m-%d", time.localtime(prog.created_at))
                    line = (f"{prog.name:<30}  {prog.mode_key:<12}"
                            f"  {mins:02d}:{secs:02d}  {date}")
                    col  = _LIB_TEXT if is_sel else _LIB_DIM_TEXT
                    surf = item_font.render(line, True, col)
                    screen.blit(surf, (modal_x + 10, ry + 10))

        # Feedback line
        if feedback and (time.monotonic() - feedback_t) < 2.0:
            if item_font:
                fb = item_font.render(feedback, True, _LIB_DELETE)
                screen.blit(fb, (modal_x + 12, modal_y + modal_h - 28))

        pygame.display.flip()
        clock.tick(30)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="LightBrain Song Preview (Sprint 4)")
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

    store         = ProgramStore(PROGRAMS_DIR)
    setlist_store = _SetlistStore(SETLISTS_DIR)
    sample_rate   = 44100
    song_file_path = ""

    if args.file and not args.simulate:
        from audio.file_loader import load_audio_file
        print(f"[Preview] Loading {args.file} …")
        audio, sample_rate = load_audio_file(args.file)
        song_file_path = os.path.abspath(args.file)
        print(f"[Preview] Loaded {len(audio)/sample_rate:.1f}s at {sample_rate} Hz")
    else:
        print(f"[Preview] Generating {args.duration:.0f}s synthetic audio …")
        audio = _build_synthetic_audio(args.duration, sample_rate)

    # Auto-match: check if we already have a saved program for this audio
    _auto_match = store.find_by_fingerprint(
        compute_song_fingerprint(audio, sample_rate)
    )
    if _auto_match:
        print(f"[Preview] Auto-matched saved program: '{_auto_match.name}' "
              f"(mode={_auto_match.settings.mode_key if _auto_match.settings else '?'})")

    # Setlist auto-detection: report track position if song is in any setlist
    _fp_for_setlist = compute_song_fingerprint(audio, sample_rate)
    _setlist_hit = setlist_store.find_by_fingerprint(_fp_for_setlist)
    if _setlist_hit:
        sl, sl_entry = _setlist_hit
        print(f"[Preview] Setlist: '{sl.name}' — "
              f"Track {sl_entry.position}/{sl.entry_count()}: {sl_entry.name}")

    current_mode = args.mode if args.mode in MODES else "open_dance"
    current_seed = args.seed

    analysis_tl, fx_tl, current_settings = _run_analysis_and_generation(
        audio, sample_rate, current_mode, current_seed, palettes
    )

    # Use auto-matched program if found (override freshly-generated timeline)
    if _auto_match:
        if _auto_match.analysis_timeline:
            analysis_tl = _auto_match.analysis_timeline
        if _auto_match.fixture_state_timeline:
            fx_tl = _auto_match.fixture_state_timeline
        if _auto_match.settings:
            current_settings = _auto_match.settings
            current_mode     = _auto_match.settings.mode_key
        current_seed = _auto_match.random_seed

    # ---- Pygame setup ----
    pygame.init()
    pygame.font.init()
    screen = pygame.display.set_mode((WINDOW_W, WINDOW_H))
    pygame.display.set_caption("LightBrain Song Preview — Sprint 5")
    clock  = pygame.time.Clock()

    visualizer = Visualizer(WINDOW_W, WINDOW_H)
    waveform   = WaveformDisplay(width=WINDOW_W, height=WAVEFORM_HEIGHT)
    waveform.load(analysis_tl)

    controller = PlaybackController()
    controller.load(fx_tl)

    try:
        font_small = pygame.font.SysFont("monospace", 12)
    except Exception:
        font_small = None

    _MODE_KEYS = {
        pygame.K_o: "open_dance",
        pygame.K_d: "dinner",
        pygame.K_b: "banger",
        pygame.K_i: "indian_latin",
        pygame.K_l: "slow_dance",   # L = slow_dance (Ctrl+L = library)
    }

    phrase_times = sorted(
        e.time_s for e in analysis_tl.events if e.label == "phrase_start"
    )

    def _seek_phrase(direction: int) -> None:
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
        nonlocal analysis_tl, fx_tl, current_mode, current_seed, current_settings, phrase_times
        current_mode = mode_key
        current_seed = seed
        _, fx_tl_new, settings_new = _run_analysis_and_generation(
            audio, sample_rate, mode_key, seed, palettes
        )
        fx_tl            = fx_tl_new
        current_settings = settings_new
        controller.load(fx_tl)
        phrase_times = sorted(
            e.time_s for e in analysis_tl.events if e.label == "phrase_start"
        )

    # Notification string displayed briefly at top of controls bar
    _notify_msg = ""
    _notify_t   = 0.0

    def _notify(msg: str) -> None:
        nonlocal _notify_msg, _notify_t
        _notify_msg = msg
        _notify_t   = time.monotonic()

    running = True

    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False

            elif event.type == pygame.KEYDOWN:
                k    = event.key
                ctrl = bool(event.mod & pygame.KMOD_CTRL)

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

                elif k == pygame.K_r and not ctrl:
                    _rerun(current_mode, current_seed)

                elif k == pygame.K_s and ctrl:
                    # Ctrl+S → save program
                    pid = _save_program(
                        store, audio, sample_rate, song_file_path,
                        analysis_tl, fx_tl, current_settings, current_seed,
                    )
                    _notify(f"Saved  (id {pid[:8]}…)")

                elif k == pygame.K_l and ctrl:
                    # Ctrl+L → open library
                    loaded = _show_program_library(screen, store, font_small)
                    if loaded is not None:
                        # Try to reload original audio from stored path
                        reloaded = _try_reload_audio(loaded.song_file_path)
                        if reloaded:
                            audio_new, sr_new = reloaded
                        else:
                            audio_new, sr_new = audio, sample_rate

                        analysis_tl      = loaded.analysis_timeline or analysis_tl
                        fx_tl            = loaded.fixture_state_timeline or fx_tl
                        current_settings = loaded.settings or current_settings
                        current_mode     = (loaded.settings.mode_key
                                            if loaded.settings else current_mode)
                        current_seed     = loaded.random_seed
                        song_file_path   = loaded.song_file_path
                        audio            = audio_new
                        sample_rate      = sr_new

                        controller.load(fx_tl)
                        waveform.load(analysis_tl)
                        phrase_times = sorted(
                            e.time_s for e in analysis_tl.events
                            if e.label == "phrase_start"
                        )
                        _notify(f"Loaded  '{loaded.name}'")

                elif k == pygame.K_s and not ctrl:
                    # bare S = slow_dance mode (legacy)
                    _rerun("slow_dance", current_seed)

                elif k in _MODE_KEYS and not ctrl:
                    _rerun(_MODE_KEYS[k], current_seed)

        # ---- Update playback ----
        rig_state = controller.update()

        # ---- Render ----
        screen.fill((0, 0, 0))
        if rig_state is not None:
            visualizer.draw(screen, rig_state)

        waveform.draw(screen, x=0, y=WAVEFORM_Y,
                      current_time_s=controller.current_time_s)

        _draw_controls(screen, controller, current_mode, current_seed,
                       font_small, CONTROLS_Y,
                       notify=_notify_msg if (time.monotonic() - _notify_t) < 3.0 else "")

        pygame.display.flip()
        clock.tick(40)

    pygame.quit()
    print("Song Preview closed.")


def _draw_controls(screen, controller, mode_key, seed, font, y, notify=""):
    """Draw a minimal playback controls bar."""
    try:
        w = screen.get_width()
        pygame.draw.rect(screen, (20, 20, 35), (0, y, w, 30))

        bar_x, bar_y = 120, y + 10
        bar_w, bar_h = w - 380, 10
        pygame.draw.rect(screen, (50, 50, 80),    (bar_x, bar_y, bar_w, bar_h))
        fill_w = int(controller.progress * bar_w)
        pygame.draw.rect(screen, (100, 150, 255), (bar_x, bar_y, fill_w, bar_h))

        if font:
            play_sym = "▶" if not controller.is_playing else "⏸"
            if notify:
                label = f"{play_sym}  {notify}"
            else:
                label = (f"{play_sym}  {mode_key:<12}  seed:{seed}"
                         f"  Space=▶/⏸  ←→=5s  []=phrase  R=rerun"
                         f"  ^S=save  ^L=library  Q=quit")
            surf = font.render(label, True, (160, 160, 200))
            screen.blit(surf, (4, y + 8))
    except Exception:
        pass


if __name__ == "__main__":
    main()
