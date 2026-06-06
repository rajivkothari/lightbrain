"""
Terminal diagnostic overlay for LightBrain Sprint 1.

Renders a fixed-position, refreshing display at ~20-40 FPS using ANSI
cursor control. Designed to stay in place without scrolling.

Shows:
  - Audio input device and FPS
  - Raw band energies with colour-coded bar graphs
  - Smoothed lane values (Impact, Room)
  - Mode, palette, HSV and RGB output
  - RockWedge DMX channels 1-8 with mini bars
  - Safety state (blackout, strobe)
  - Any active error message
"""

import sys
import time
from typing import Optional

try:
    import colorama
    colorama.init()
except ImportError:
    pass

import colorsys


# ---------------------------------------------------------------------------
# ANSI helpers
# ---------------------------------------------------------------------------

def _esc(code: str) -> str:
    return f"\033[{code}"

CLEAR_SCREEN = _esc("2J")
CURSOR_HOME  = _esc("H")
RESET        = _esc("0m")
BOLD         = _esc("1m")
DIM          = _esc("2m")
FG_CYAN      = _esc("96m")
FG_GREEN     = _esc("92m")
FG_YELLOW    = _esc("93m")
FG_RED       = _esc("91m")
FG_MAGENTA   = _esc("95m")
FG_WHITE     = _esc("97m")


def _bar(value: float, width: int = 26) -> str:
    """Plain ASCII progress bar, value 0.0–1.0."""
    filled = int(max(0.0, min(1.0, value)) * width)
    return "[" + "#" * filled + "." * (width - filled) + "]"


def _colored_bar(value: float, width: int = 26) -> str:
    """Colour-coded progress bar: cyan→green→yellow→red."""
    if value > 0.80:
        color = FG_RED
    elif value > 0.50:
        color = FG_YELLOW
    elif value > 0.20:
        color = FG_GREEN
    else:
        color = FG_CYAN
    return f"{color}{_bar(value, width)}{RESET}"


def _rgb_swatch(r: int, g: int, b: int) -> str:
    """Four-space block filled with 24-bit ANSI colour."""
    try:
        return f"\033[48;2;{r};{g};{b}m    \033[0m"
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Overlay
# ---------------------------------------------------------------------------

# All lines must render within this visible width (ignoring ANSI codes).
# Adjust if your terminal is narrower.
WIDTH = 62

_SEP  = f"{DIM}{'─' * WIDTH}{RESET}"


def _section(label: str) -> str:
    """Section header: ── LABEL ─────────────────"""
    right = WIDTH - len(label) - 4
    return f"{BOLD}{FG_CYAN}── {label} {'─' * max(right, 0)}{RESET}"


class TerminalDebugOverlay:
    """
    Fixed-position terminal overlay — call update() each frame.

    The display stays in place by moving the cursor to the top of the
    screen before every render. init_screen() must be called once before
    the first update() to clear the terminal and hide the cursor.
    """

    def __init__(self):
        self._frame_count  = 0
        self._last_time:   Optional[float] = None   # None = not started
        self._fps          = 0.0
        self._fps_samples  = []
        self._initialized  = False

    # ------------------------------------------------------------------
    # FPS tracking
    # ------------------------------------------------------------------

    def _update_fps(self) -> None:
        now = time.monotonic()
        if self._last_time is None:
            # First call — record the start time but don't emit a sample;
            # the interval from __init__ to first frame is not meaningful.
            self._last_time = now
            return
        dt = now - self._last_time
        self._last_time = now
        if dt > 0:
            self._fps_samples.append(1.0 / dt)
            # Keep a 20-frame rolling window
            if len(self._fps_samples) > 20:
                self._fps_samples.pop(0)
            self._fps = sum(self._fps_samples) / len(self._fps_samples)

    # ------------------------------------------------------------------
    # Main render
    # ------------------------------------------------------------------

    def update(
        self,
        device_name:      str            = "unknown",
        raw_bands:        Optional[dict] = None,
        smoothed_lanes:   Optional[dict] = None,
        mode_name:        str            = "—",
        palette_name:     str            = "—",
        hsv:              Optional[tuple] = None,   # (h, s, v)
        rgb:              Optional[tuple] = None,   # (r, g, b) 0-255
        fixture_name:     str            = "RockWedge LED",
        dmx_address:      int            = 1,
        dmx_channels:     Optional[dict] = None,    # {label: 0-255}
        dmx_output_type:  str            = "MOCK",
        safety_blackout:  bool           = False,
        safety_strobe_ok: bool           = False,
        error:            Optional[str]  = None,
    ) -> None:
        """Render one frame of the diagnostic overlay to stdout."""

        self._frame_count += 1
        self._update_fps()

        raw_bands      = raw_bands      or {}
        smoothed_lanes = smoothed_lanes or {}
        dmx_channels   = dmx_channels   or {}
        hsv            = hsv            or (0.0, 0.0, 0.0)
        rgb            = rgb            or (0,   0,   0)

        lines = []

        # ── Header ──────────────────────────────────────────────────────
        title = "LIGHTBRAIN  DIAGNOSTIC  OVERLAY  S1"
        pad   = max(0, WIDTH - len(title))
        lines.append(f"{BOLD}{FG_CYAN}{'─' * (pad // 2)} {title} {'─' * (pad - pad // 2)}{RESET}")

        dmx_color = FG_GREEN if dmx_output_type == "MOCK" else FG_YELLOW
        dev_trunc = (device_name[:32] + "…") if len(device_name) > 33 else device_name
        fps_str   = f"{self._fps:5.1f}" if self._fps > 0 else "  ---"
        lines.append(
            f" {FG_WHITE}IN:{RESET} {dev_trunc:<33}"
            f"  {FG_WHITE}FPS:{RESET}{FG_GREEN}{fps_str}{RESET}"
            f"  {FG_WHITE}DMX:{RESET}{dmx_color}{dmx_output_type}{RESET}"
        )

        # ── Raw bands ───────────────────────────────────────────────────
        lines.append(_section("RAW BANDS"))

        def _band(label: str, key: str) -> str:
            val = raw_bands.get(key, 0.0)
            return (f"  {FG_WHITE}{label:<8}{RESET}"
                    f" {_colored_bar(val, 26)} {FG_YELLOW}{val:.2f}{RESET}")

        lines.append(_band("LOW",    "low_energy"))
        lines.append(_band("MID",    "mid_energy"))
        lines.append(_band("HIGH",   "high_energy"))
        lines.append(_band("ENERGY", "overall_energy"))

        # ── Smoothed lanes ──────────────────────────────────────────────
        lines.append(_section("SMOOTHED LANES"))

        def _lane(label: str, key: str) -> str:
            val = smoothed_lanes.get(key, 0.0)
            return (f"  {FG_WHITE}{label:<8}{RESET}"
                    f" {_colored_bar(val, 26)} {FG_YELLOW}{val:.2f}{RESET}")

        lines.append(_lane("IMPACT", "impact"))
        lines.append(_lane("ROOM",   "room"))

        # ── Mode / palette ──────────────────────────────────────────────
        lines.append(_section("MODE / PALETTE"))

        bo_tag = (f" {FG_RED}{BOLD}[BLACKOUT]{RESET}" if safety_blackout else "")
        lines.append(f"  {FG_WHITE}Mode:{RESET}    {FG_MAGENTA}{mode_name}{RESET}{bo_tag}")
        lines.append(f"  {FG_WHITE}Palette:{RESET} {FG_CYAN}{palette_name}{RESET}")

        h, s, v_hsv = hsv
        r,  g,  b   = rgb
        lines.append(f"  {FG_WHITE}HSV:{RESET}     {h:5.1f}°  S:{s:.2f}  V:{v_hsv:.2f}")
        lines.append(f"  {FG_WHITE}RGB:{RESET}     {r:3d},{g:3d},{b:3d}  {_rgb_swatch(r, g, b)}")

        # ── Output target ───────────────────────────────────────────────
        lines.append(_section("OUTPUT TARGET"))
        lines.append(f"  {FG_WHITE}Fixture:{RESET} {fixture_name}  "
                     f"{FG_WHITE}Addr:{RESET} {dmx_address:03d}")

        ch_order = ["Dimmer", "Red", "Green", "Blue",
                    "White",  "Amber", "UV",  "Strobe"]
        for i, label in enumerate(ch_order):
            val    = dmx_channels.get(label, 0)
            mini   = _bar(val / 255.0, width=14)
            ch_num = dmx_address + i
            lines.append(
                f"  {FG_WHITE}Ch{ch_num:<2}{RESET} {label:<7}"
                f" {FG_GREEN}{mini}{RESET}  {val:3d}"
            )

        # ── Safety ──────────────────────────────────────────────────────
        lines.append(_section("SAFETY"))
        bo_s  = f"{FG_RED}ON {RESET}" if safety_blackout  else f"{FG_GREEN}OFF{RESET}"
        str_s = f"{FG_YELLOW}OK {RESET}" if safety_strobe_ok else f"{DIM}OFF{RESET}"
        lines.append(f"  Blackout: {bo_s}   Strobe: {str_s}")

        if error:
            lines.append(f"  {FG_RED}ERR: {error[:WIDTH - 8]}{RESET}")

        lines.append(_SEP)

        # Move to top and write everything in one syscall
        out = CURSOR_HOME + "\n".join(lines) + "\n"
        sys.stdout.write(out)
        sys.stdout.flush()

    # ------------------------------------------------------------------
    # Setup / teardown
    # ------------------------------------------------------------------

    def init_screen(self) -> None:
        """Clear terminal and hide cursor. Call once before the loop."""
        if not self._initialized:
            sys.stdout.write(CLEAR_SCREEN + CURSOR_HOME)
            sys.stdout.write("\033[?25l")   # hide cursor
            sys.stdout.flush()
            self._initialized = True

    def restore_screen(self) -> None:
        """Re-show cursor and leave the terminal in a clean state."""
        sys.stdout.write("\033[?25h")       # show cursor
        sys.stdout.write("\n")
        sys.stdout.flush()
