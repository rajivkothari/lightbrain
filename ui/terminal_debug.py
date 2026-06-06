"""
Terminal diagnostic overlay for LightBrain Sprint 1.

Renders a clear, refreshing display at ~20-40 FPS showing:
- Audio input device and FPS
- Raw band energies with bar graphs
- Smoothed lane values
- Mode and palette state
- Current HSV and RGB color
- RockWedge DMX channels 1-8
- Safety state
- Any active errors

Uses ANSI escape codes via colorama for cursor positioning and color.
Falls back gracefully if colorama is unavailable.
"""

import os
import sys
import time
from typing import Optional

try:
    import colorama
    colorama.init()
    COLORAMA_OK = True
except ImportError:
    COLORAMA_OK = False

import colorsys


# ANSI escape helpers
def _esc(code: str) -> str:
    return f"\033[{code}"

CLEAR_SCREEN  = _esc("2J")
CURSOR_HOME   = _esc("H")
CURSOR_UP     = _esc("F")
RESET         = _esc("0m")
BOLD          = _esc("1m")
DIM           = _esc("2m")
FG_CYAN       = _esc("96m")
FG_GREEN      = _esc("92m")
FG_YELLOW     = _esc("93m")
FG_RED        = _esc("91m")
FG_MAGENTA    = _esc("95m")
FG_WHITE      = _esc("97m")
FG_BLUE       = _esc("94m")


def _bar(value: float, width: int = 28) -> str:
    """Render a simple ASCII progress bar."""
    filled = int(max(0.0, min(1.0, value)) * width)
    return "[" + "#" * filled + "." * (width - filled) + "]"


def _colored_bar(value: float, width: int = 28) -> str:
    """Progress bar with color based on value level."""
    if value > 0.8:
        color = FG_RED
    elif value > 0.5:
        color = FG_YELLOW
    elif value > 0.2:
        color = FG_GREEN
    else:
        color = FG_CYAN
    return f"{color}{_bar(value, width)}{RESET}"


class TerminalDebugOverlay:
    """
    Renders the LightBrain diagnostic overlay by writing directly
    to stdout using ANSI cursor control.

    Call update() each frame with the current engine state.
    """

    OVERLAY_WIDTH = 65

    def __init__(self):
        self._frame_count = 0
        self._last_time   = time.monotonic()
        self._fps         = 0.0
        self._fps_samples = []
        self._initialized = False
        self._error: Optional[str] = None

    def _divider(self, label: str = "") -> str:
        if label:
            pad = self.OVERLAY_WIDTH - len(label) - 4
            return f"{BOLD}{FG_CYAN}== {label} {'=' * pad}{RESET}"
        return f"{DIM}{'=' * self.OVERLAY_WIDTH}{RESET}"

    def _header(self) -> str:
        return (f"{BOLD}{FG_CYAN}"
                f"{'=' * 5} LIGHTBRAIN DIAGNOSTIC OVERLAY S1 {'=' * 5}"
                f"{RESET}")

    def _update_fps(self) -> None:
        now = time.monotonic()
        dt  = now - self._last_time
        self._last_time = now
        if dt > 0:
            self._fps_samples.append(1.0 / dt)
            if len(self._fps_samples) > 20:
                self._fps_samples.pop(0)
            self._fps = sum(self._fps_samples) / len(self._fps_samples)

    def update(
        self,
        # Audio
        device_name: str = "unknown",
        raw_bands: Optional[dict] = None,
        # Lanes
        smoothed_lanes: Optional[dict] = None,
        # Mode / palette
        mode_name: str = "—",
        palette_name: str = "—",
        hsv: Optional[tuple] = None,      # (h, s, v)
        rgb: Optional[tuple] = None,      # (r, g, b) 0-255
        # Fixture
        fixture_name: str = "RockWedge LED",
        dmx_address: int = 1,
        dmx_channels: Optional[dict] = None,  # {label: value}
        # System
        dmx_output_type: str = "MOCK",
        safety_blackout: bool = False,
        safety_strobe_ok: bool = False,
        error: Optional[str] = None,
    ) -> None:
        """Render one frame of the diagnostic overlay."""

        self._frame_count += 1
        self._update_fps()

        raw_bands       = raw_bands or {}
        smoothed_lanes  = smoothed_lanes or {}
        dmx_channels    = dmx_channels or {}
        hsv             = hsv or (0.0, 0.0, 0.0)
        rgb             = rgb or (0, 0, 0)

        lines = []

        # Header
        lines.append(self._header())
        dmx_color = FG_GREEN if dmx_output_type == "MOCK" else FG_YELLOW
        lines.append(
            f" {FG_WHITE}AUDIO:{RESET} {device_name[:30]:<30}  "
            f"{FG_WHITE}FPS:{RESET} {FG_GREEN}{self._fps:4.1f}{RESET}  "
            f"{FG_WHITE}DMX:{RESET} {dmx_color}{dmx_output_type}{RESET}"
        )
        lines.append(self._divider("RAW BANDS"))

        def band_line(label, key):
            val = raw_bands.get(key, 0.0)
            return (f"  {FG_WHITE}{label:<12}{RESET} "
                    f"{_colored_bar(val)} {FG_YELLOW}{val:.2f}{RESET}")

        lines.append(band_line("LOW",    "low_energy"))
        lines.append(band_line("MID",    "mid_energy"))
        lines.append(band_line("HIGH",   "high_energy"))
        lines.append(band_line("ENERGY", "overall_energy"))

        lines.append(self._divider("SMOOTHED LANES"))

        def lane_line(label, key):
            val = smoothed_lanes.get(key, 0.0)
            return (f"  {FG_WHITE}{label:<12}{RESET} "
                    f"{_colored_bar(val)} {FG_YELLOW}{val:.2f}{RESET}")

        lines.append(lane_line("IMPACT", "impact"))
        lines.append(lane_line("ROOM",   "room"))

        lines.append(self._divider("MODE / PALETTE"))

        bo_str = (f"  {FG_RED}{BOLD}*** BLACKOUT ACTIVE ***{RESET}"
                  if safety_blackout else "")
        lines.append(f"  {FG_WHITE}MODE:{RESET}    {FG_MAGENTA}{mode_name}{RESET}{bo_str}")
        lines.append(f"  {FG_WHITE}PALETTE:{RESET} {FG_CYAN}{palette_name}{RESET}")

        h, s, v = hsv
        r, g, b = rgb
        lines.append(f"  {FG_WHITE}HSV:{RESET}     {h:.0f}°, {s:.2f}, {v:.2f}")
        lines.append(f"  {FG_WHITE}RGB:{RESET}     {r:3d}, {g:3d}, {b:3d}  "
                     + _rgb_swatch(r, g, b))

        lines.append(self._divider("OUTPUT TARGET"))
        lines.append(f"  {FG_WHITE}Fixture:{RESET}  {fixture_name}")
        lines.append(f"  {FG_WHITE}Address:{RESET}  {dmx_address:03d}")

        ch_labels = ["Dimmer", "Red", "Green", "Blue",
                     "White", "Amber", "UV", "Strobe"]
        for i, label in enumerate(ch_labels):
            val = dmx_channels.get(label, 0)
            bar = _bar(val / 255.0, width=16)
            ch_num = dmx_address + i
            lines.append(
                f"  Ch{i+1} {FG_WHITE}{label:<8}{RESET}  "
                f"{FG_GREEN}{bar}{RESET}  {val:3d}"
            )

        lines.append(self._divider("SAFETY"))
        bo_state = f"{FG_RED}ON{RESET}" if safety_blackout else f"{FG_GREEN}OFF{RESET}"
        st_state = f"{FG_YELLOW}OK{RESET}" if safety_strobe_ok else f"{DIM}DISABLED{RESET}"
        lines.append(f"  Blackout: {bo_state}    Strobe: {st_state}")

        if error:
            lines.append(f"  {FG_RED}ERROR: {error}{RESET}")

        lines.append(f"{DIM}{'=' * self.OVERLAY_WIDTH}{RESET}")

        # Move cursor to top and render
        sys.stdout.write(CURSOR_HOME)
        output = "\n".join(lines) + "\n"
        sys.stdout.write(output)
        sys.stdout.flush()

    def init_screen(self) -> None:
        """Clear screen and hide cursor for a clean overlay."""
        if not self._initialized:
            sys.stdout.write(CLEAR_SCREEN)
            sys.stdout.write("\033[?25l")  # hide cursor
            sys.stdout.flush()
            self._initialized = True

    def restore_screen(self) -> None:
        """Show cursor and print a newline on exit."""
        sys.stdout.write("\033[?25h")  # show cursor
        sys.stdout.write("\n")
        sys.stdout.flush()


def _rgb_swatch(r: int, g: int, b: int) -> str:
    """Return a small colored block using ANSI 24-bit color if available."""
    try:
        return f"\033[48;2;{r};{g};{b}m    \033[0m"
    except Exception:
        return ""
