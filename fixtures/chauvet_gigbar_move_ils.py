"""
Chauvet DJ GigBAR Move + ILS — 29-channel and 51-channel DMX mapper.

Set the fixture's DMX personality to match the `personality` key in
rig_config.json ("29ch" or "51ch").

────────────────────────────────────────────────────────────────────────
29Ch layout (shared par bank — one set of controls for all 4 pars):
  Ch  1   Par Red          0–255
  Ch  2   Par Green        0–255
  Ch  3   Par Blue         0–255
  Ch  4   Par Amber        0–255
  Ch  5   Par White        0–255
  Ch  6   Par UV           0–255
  Ch  7   Par Strobe       0–250=slow–fast, 251–255=sound-active
  Ch  8   Derby Red        0–255
  Ch  9   Derby Green      0–255
  Ch 10   Derby Blue       0–255
  Ch 11   Derby White      0–255
  Ch 12   Derby Strobe     0–250=slow–fast, 251–255=sound-active
  Ch 13   Derby Rotation   0=stop, 1–127=CW slow–fast, 128=stop, 129–255=CCW slow–fast
  Ch 14   Flash LED 1      0–255
  Ch 15   Flash LED 2      0–255
  Ch 16   Flash LED 3      0–255
  Ch 17   Flash LED 4      0–255
  Ch 18   Flash Strobe     0–250=slow–fast, 251–255=sound-active
  Ch 19   Laser control    0=blackout, 1–255=colors/patterns
  Ch 20   Laser patterns   0–255
  Ch 21   Spot Pan         0–255 (0=0°, 128≈270°, 255=540°)
  Ch 22   Spot Fine Pan    0–255
  Ch 23   Spot Tilt        0–255
  Ch 24   Spot Fine Tilt   0–255
  Ch 25   Spot Pan/Tilt Speed   0–255 (0=fastest, 255=slowest)
  Ch 26   Spot Color Wheel 0–6=white, 7–13=red, 14–20=orange, 21–27=yellow,
                           28–34=green, 35–41=blue, 42–48=CTO, 49–55=cyan,
                           56–62=magenta, 63–64=lime, 65–189=color index,
                           190–255=scroll
  Ch 27   Spot Gobo Wheel  0–5=open, 6–117=gobos 1–9, …, 128–255=scroll
  Ch 28   Spot Dimmer      0–255
  Ch 29   Spot Strobe      0–3=closed, 4–7=open, 8–76=strobe slow–fast,
                           77–145=pulse, 146–215=random, 216–255=open

────────────────────────────────────────────────────────────────────────
51Ch layout (individual par bank — each of the 4 pars controlled separately):
  Ch  1– 7   PAR 1  Red, Green, Blue, Amber, White, UV, Strobe
  Ch  8–14   PAR 2  Red, Green, Blue, Amber, White, UV, Strobe
  Ch 15–21   PAR 3  Red, Green, Blue, Amber, White, UV, Strobe
  Ch 22–28   PAR 4  Red, Green, Blue, Amber, White, UV, Strobe
  Ch 29   Derby Red        0–255
  Ch 30   Derby Green      0–255
  Ch 31   Derby Blue       0–255
  Ch 32   Derby White      0–255
  Ch 33   Derby Strobe     0–250=slow–fast, 251–255=sound-active
  Ch 34   Derby Rotation   0=stop, 1–127=CW slow–fast, 128=stop, 129–255=CCW slow–fast
  Ch 35   Flash LED 1      0–255
  Ch 36   Flash LED 2      0–255
  Ch 37   Flash LED 3      0–255
  Ch 38   Flash LED 4      0–255
  Ch 39   Flash Strobe     0–250=slow–fast, 251–255=sound-active
  Ch 40   Laser Color      0=blackout, 1–255=colors/patterns
  Ch 41   Laser Pattern    0–255
  Ch 42   Spot Pan         0–255 (0=0°, 255=540°)
  Ch 43   Spot Fine Pan    0–255
  Ch 44   Spot Tilt        0–255
  Ch 45   Spot Fine Tilt   0–255
  Ch 46   Spot Pan/Tilt Speed   0–255 (0=fastest, 255=slowest)
  Ch 47   Spot Color Wheel (same map as 29Ch Ch 26)
  Ch 48   Spot Gobo Wheel  (same map as 29Ch Ch 27)
  Ch 49   Spot Dimmer      0–255
  Ch 50   Spot Strobe      (same map as 29Ch Ch 29)
  Ch 51   ILS              0–7=ILS off, 8–255=ILS enabled / sync master

────────────────────────────────────────────────────────────────────────
Sections
--------
Par wash   — audio-reactive colour matching the room lane (RGBAWUV).
             In 51Ch mode all 4 pars receive identical values.
Derby      — same hue as pars; rotates proportionally to room energy.
Flash LEDs — ambient white fill at a fraction of room brightness.
Laser      — OFF (0) by default; enable per-event by calling enable_laser().
Spot heads — fixed aim position (configurable via rig_config.json or
             set_spot_aim()); dimmer tracks room brightness; colour wheel open.
ILS        — always 0 (ILS off); can be overridden per-event if needed.
"""

import colorsys
import warnings
from dataclasses import dataclass, field
from typing import Optional

from dmx.universe import DMXUniverse
from engine.gamma import apply_gamma_to_dmx
from fixtures.fixture import FixtureBase


# ── 29Ch channel offsets (0-indexed from dmx_address) ─────────────────────

# Par (Ch 1–7)
CH29_PAR_RED    = 0
CH29_PAR_GREEN  = 1
CH29_PAR_BLUE   = 2
CH29_PAR_AMBER  = 3
CH29_PAR_WHITE  = 4
CH29_PAR_UV     = 5
CH29_PAR_STROBE = 6

# Derby (Ch 8–13)
CH29_DERBY_RED      = 7
CH29_DERBY_GREEN    = 8
CH29_DERBY_BLUE     = 9
CH29_DERBY_WHITE    = 10
CH29_DERBY_STROBE   = 11
CH29_DERBY_ROTATION = 12

# Flash LEDs (Ch 14–18)
CH29_FLASH_1      = 13
CH29_FLASH_2      = 14
CH29_FLASH_3      = 15
CH29_FLASH_4      = 16
CH29_FLASH_STROBE = 17

# Laser (Ch 19–20)
CH29_LASER_COLOR   = 18
CH29_LASER_PATTERN = 19

# Spot (Ch 21–29)
CH29_SPOT_PAN       = 20
CH29_SPOT_PAN_FINE  = 21
CH29_SPOT_TILT      = 22
CH29_SPOT_TILT_FINE = 23
CH29_SPOT_SPEED     = 24
CH29_SPOT_COLOR     = 25
CH29_SPOT_GOBO      = 26
CH29_SPOT_DIMMER    = 27
CH29_SPOT_STROBE    = 28

NUM_CHANNELS_29 = 29

# ── 51Ch channel offsets (0-indexed from dmx_address) ─────────────────────

# PAR 1 (Ch 1–7)
CH51_PAR1_RED    = 0
CH51_PAR1_GREEN  = 1
CH51_PAR1_BLUE   = 2
CH51_PAR1_AMBER  = 3
CH51_PAR1_WHITE  = 4
CH51_PAR1_UV     = 5
CH51_PAR1_STROBE = 6

# PAR 2 (Ch 8–14)
CH51_PAR2_RED    = 7
CH51_PAR2_GREEN  = 8
CH51_PAR2_BLUE   = 9
CH51_PAR2_AMBER  = 10
CH51_PAR2_WHITE  = 11
CH51_PAR2_UV     = 12
CH51_PAR2_STROBE = 13

# PAR 3 (Ch 15–21)
CH51_PAR3_RED    = 14
CH51_PAR3_GREEN  = 15
CH51_PAR3_BLUE   = 16
CH51_PAR3_AMBER  = 17
CH51_PAR3_WHITE  = 18
CH51_PAR3_UV     = 19
CH51_PAR3_STROBE = 20

# PAR 4 (Ch 22–28)
CH51_PAR4_RED    = 21
CH51_PAR4_GREEN  = 22
CH51_PAR4_BLUE   = 23
CH51_PAR4_AMBER  = 24
CH51_PAR4_WHITE  = 25
CH51_PAR4_UV     = 26
CH51_PAR4_STROBE = 27

# Derby (Ch 29–34)
CH51_DERBY_RED      = 28
CH51_DERBY_GREEN    = 29
CH51_DERBY_BLUE     = 30
CH51_DERBY_WHITE    = 31
CH51_DERBY_STROBE   = 32
CH51_DERBY_ROTATION = 33

# Flash LEDs (Ch 35–39)
CH51_FLASH_1      = 34
CH51_FLASH_2      = 35
CH51_FLASH_3      = 36
CH51_FLASH_4      = 37
CH51_FLASH_STROBE = 38

# Laser (Ch 40–41)
CH51_LASER_COLOR   = 39
CH51_LASER_PATTERN = 40

# Spot (Ch 42–50)
CH51_SPOT_PAN       = 41
CH51_SPOT_PAN_FINE  = 42
CH51_SPOT_TILT      = 43
CH51_SPOT_TILT_FINE = 44
CH51_SPOT_SPEED     = 45
CH51_SPOT_COLOR     = 46
CH51_SPOT_GOBO      = 47
CH51_SPOT_DIMMER    = 48
CH51_SPOT_STROBE    = 49

# ILS (Ch 51)
CH51_ILS = 50

NUM_CHANNELS_51 = 51

# ── Shared constants ───────────────────────────────────────────────────────

# Par / Derby / Flash strobe: 0=off, 1=slowest, 250=fastest
_PAR_STROBE_MIN = 1
_PAR_STROBE_MAX = 250

# Spot strobe: 4–7=open (no strobe), 8–76=strobe slow–fast
_SPOT_STROBE_OPEN = 4
_SPOT_STROBE_MIN  = 8
_SPOT_STROBE_MAX  = 76

# Derby rotation: 129–255=CCW slow–fast; 0=stop
_DERBY_ROTATE_CCW_BASE = 129

# Flash ambient fill level (fraction of par brightness)
_FLASH_FILL_RATIO = 0.15

# Default spot aim: pointing toward the dance floor
_DEFAULT_SPOT_PAN_DEG  = 270.0
_DEFAULT_SPOT_TILT_DMX = 90
_SPOT_PAN_RANGE_DEG    = 270.0


def _par_strobe(strobe_0_1: float) -> int:
    if strobe_0_1 <= 0.0:
        return 0
    return _PAR_STROBE_MIN + int(
        max(0.0, min(1.0, strobe_0_1)) * (_PAR_STROBE_MAX - _PAR_STROBE_MIN)
    )


def _spot_strobe_dmx(strobe_0_1: float) -> int:
    if strobe_0_1 <= 0.0:
        return _SPOT_STROBE_OPEN
    return _SPOT_STROBE_MIN + int(
        max(0.0, min(1.0, strobe_0_1)) * (_SPOT_STROBE_MAX - _SPOT_STROBE_MIN)
    )


def _pan_deg_to_dmx(pan_deg: float) -> int:
    """540° range: 0°=0, 270°=128, 540°=255."""
    return int(max(0.0, min(1.0, pan_deg / 540.0)) * 255)


class ChauvetGigBarMoveILS(FixtureBase):
    """
    Chauvet DJ GigBAR Move + ILS in 29-channel or 51-channel mode.

    Set `personality` to "29ch" or "51ch" to match the fixture's menu setting.
    In 51ch mode all 4 PAR fixtures receive the same colour values.

    Call render_to_universe() once per frame with the room lane values.
    """

    VALID_PERSONALITIES = ("29ch", "51ch")

    def __init__(
        self,
        fixture_id:     str,
        name:           str,
        dmx_address:    int,
        lane:           str   = "room",
        group:          str   = "all",
        personality:    str   = "51ch",
        spot_pan_deg:   float = _DEFAULT_SPOT_PAN_DEG,
        spot_tilt_dmx:  int   = _DEFAULT_SPOT_TILT_DMX,
        laser_enabled:  bool  = False,
    ):
        super().__init__(
            fixture_id=fixture_id,
            name=name,
            dmx_address=dmx_address,
            lane=lane,
            group=group,
        )
        _p = str(personality).lower().strip()
        if _p not in self.VALID_PERSONALITIES:
            warnings.warn(
                f"ChauvetGigBarMoveILS '{name}': unknown personality {personality!r}; "
                f"defaulting to '51ch'. Valid options: {self.VALID_PERSONALITIES}",
                RuntimeWarning, stacklevel=2,
            )
            _p = "51ch"
        self._personality   = _p
        self._spot_pan_deg  = float(spot_pan_deg)
        self._spot_tilt_dmx = int(spot_tilt_dmx)
        self._laser_enabled = laser_enabled
        self._derby_enabled = True
        self._mover_only    = False
        self._spot_color_dmx: int = 0

    # ── Public controls ──────────────────────────────────────────────────

    @property
    def personality(self) -> str:
        return self._personality

    def set_spot_aim(self, pan_deg: float, tilt_dmx: int) -> None:
        self._spot_pan_deg  = float(pan_deg)
        self._spot_tilt_dmx = max(0, min(255, int(tilt_dmx)))

    def enable_laser(self, enabled: bool = True) -> None:
        self._laser_enabled = enabled

    def set_derby_enabled(self, enabled: bool) -> None:
        self._derby_enabled = bool(enabled)

    def set_mover_only(self, enabled: bool) -> None:
        """Solo the spot head — zero Par, Derby, Flash, and Laser."""
        self._mover_only = bool(enabled)

    def set_spot_color(self, dmx_val: int) -> None:
        """Set the spot color wheel. 0=open/white, 45=CTO warm white."""
        self._spot_color_dmx = max(0, min(255, int(dmx_val)))

    # ── Core render ──────────────────────────────────────────────────────

    @property
    def channel_count(self) -> int:
        return NUM_CHANNELS_51 if self._personality == "51ch" else NUM_CHANNELS_29

    def render_to_universe(
        self,
        universe:   DMXUniverse,
        brightness: float = 1.0,
        hue:        float = 0.0,
        saturation: float = 1.0,
        value:      float = 1.0,
        strobe:     float = 0.0,
        white:      float = 0.0,
        amber:      float = 0.0,
        uv:         float = 0.0,
        gamma:      float = 2.2,
    ) -> None:
        addr = self.dmx_address
        n_ch = self.channel_count
        if addr + n_ch - 1 > 512:
            warnings.warn(
                f"ChauvetGigBarMoveILS '{self.name}' at address {addr}: "
                f"channels {addr}–{addr + n_ch - 1} exceed 512",
                RuntimeWarning, stacklevel=2,
            )
        if self._personality == "51ch":
            self._render_51ch(universe, brightness, hue, saturation, value,
                              strobe, white, amber, uv, gamma)
        else:
            self._render_29ch(universe, brightness, hue, saturation, value,
                              strobe, white, amber, uv, gamma)

    # ── Internal render paths ─────────────────────────────────────────────

    def _common_values(self, brightness, hue, saturation, value, strobe,
                       white, amber, uv, gamma):
        """Compute shared DMX values used by both personalities."""
        final_v = max(0.0, min(1.0, value * brightness))
        r_f, g_f, b_f = colorsys.hsv_to_rgb(hue / 360.0, saturation, final_v)

        r_dmx  = apply_gamma_to_dmx(r_f, gamma)
        g_dmx  = apply_gamma_to_dmx(g_f, gamma)
        b_dmx  = apply_gamma_to_dmx(b_f, gamma)
        w_dmx  = apply_gamma_to_dmx(max(0.0, min(1.0, white * brightness)), gamma)
        a_dmx  = apply_gamma_to_dmx(max(0.0, min(1.0, amber * brightness)), gamma)
        uv_dmx = apply_gamma_to_dmx(max(0.0, min(1.0, uv    * brightness)), gamma)
        par_strobe_dmx = _par_strobe(strobe)

        # Derby
        if self._derby_enabled:
            dr_dmx = apply_gamma_to_dmx(r_f, gamma)
            dg_dmx = apply_gamma_to_dmx(g_f, gamma)
            db_dmx = apply_gamma_to_dmx(b_f, gamma)
            dw_dmx = apply_gamma_to_dmx(max(0.0, min(1.0, white * brightness * 0.5)), gamma)
            derby_strobe_dmx = _par_strobe(strobe)
            derby_rot_dmx = (_DERBY_ROTATE_CCW_BASE + int(final_v * 126)) if final_v > 0.05 else 0
        else:
            dr_dmx = dg_dmx = db_dmx = dw_dmx = derby_strobe_dmx = derby_rot_dmx = 0

        flash_level      = apply_gamma_to_dmx(max(0.0, min(1.0, final_v * _FLASH_FILL_RATIO)), gamma)
        flash_strobe_dmx = _par_strobe(strobe)

        laser_color_dmx   = 1 if self._laser_enabled else 0
        laser_pattern_dmx = 0

        spot_pan_dmx    = _pan_deg_to_dmx(self._spot_pan_deg)
        spot_tilt_dmx   = self._spot_tilt_dmx
        spot_speed_dmx  = 200
        spot_dimmer_dmx = apply_gamma_to_dmx(final_v, gamma)
        spot_strobe_dmx = _spot_strobe_dmx(strobe)

        if self._mover_only:
            r_dmx = g_dmx = b_dmx = a_dmx = w_dmx = uv_dmx = par_strobe_dmx = 0
            dr_dmx = dg_dmx = db_dmx = dw_dmx = derby_strobe_dmx = derby_rot_dmx = 0
            flash_level = flash_strobe_dmx = 0
            laser_color_dmx = laser_pattern_dmx = 0

        return dict(
            r_dmx=r_dmx, g_dmx=g_dmx, b_dmx=b_dmx,
            a_dmx=a_dmx, w_dmx=w_dmx, uv_dmx=uv_dmx,
            par_strobe_dmx=par_strobe_dmx,
            dr_dmx=dr_dmx, dg_dmx=dg_dmx, db_dmx=db_dmx, dw_dmx=dw_dmx,
            derby_strobe_dmx=derby_strobe_dmx, derby_rot_dmx=derby_rot_dmx,
            flash_level=flash_level, flash_strobe_dmx=flash_strobe_dmx,
            laser_color_dmx=laser_color_dmx, laser_pattern_dmx=laser_pattern_dmx,
            spot_pan_dmx=spot_pan_dmx, spot_tilt_dmx=spot_tilt_dmx,
            spot_speed_dmx=spot_speed_dmx, spot_dimmer_dmx=spot_dimmer_dmx,
            spot_strobe_dmx=spot_strobe_dmx,
        )

    def _render_29ch(self, universe, brightness, hue, saturation, value,
                     strobe, white, amber, uv, gamma):
        v = self._common_values(brightness, hue, saturation, value, strobe, white, amber, uv, gamma)
        addr = self.dmx_address
        universe.set_channels(addr, [
            v['r_dmx'],             # Ch  1 Par Red
            v['g_dmx'],             # Ch  2 Par Green
            v['b_dmx'],             # Ch  3 Par Blue
            v['a_dmx'],             # Ch  4 Par Amber
            v['w_dmx'],             # Ch  5 Par White
            v['uv_dmx'],            # Ch  6 Par UV
            v['par_strobe_dmx'],    # Ch  7 Par Strobe
            v['dr_dmx'],            # Ch  8 Derby Red
            v['dg_dmx'],            # Ch  9 Derby Green
            v['db_dmx'],            # Ch 10 Derby Blue
            v['dw_dmx'],            # Ch 11 Derby White
            v['derby_strobe_dmx'],  # Ch 12 Derby Strobe
            v['derby_rot_dmx'],     # Ch 13 Derby Rotation
            v['flash_level'],       # Ch 14 Flash LED 1
            v['flash_level'],       # Ch 15 Flash LED 2
            v['flash_level'],       # Ch 16 Flash LED 3
            v['flash_level'],       # Ch 17 Flash LED 4
            v['flash_strobe_dmx'],  # Ch 18 Flash Strobe
            v['laser_color_dmx'],   # Ch 19 Laser Color
            v['laser_pattern_dmx'], # Ch 20 Laser Pattern
            v['spot_pan_dmx'],      # Ch 21 Spot Pan
            0,                      # Ch 22 Spot Fine Pan
            v['spot_tilt_dmx'],     # Ch 23 Spot Tilt
            0,                      # Ch 24 Spot Fine Tilt
            v['spot_speed_dmx'],    # Ch 25 Spot Speed
            self._spot_color_dmx,   # Ch 26 Spot Color Wheel
            0,                      # Ch 27 Spot Gobo (open)
            v['spot_dimmer_dmx'],   # Ch 28 Spot Dimmer
            v['spot_strobe_dmx'],   # Ch 29 Spot Strobe
        ])

    def _render_51ch(self, universe, brightness, hue, saturation, value,
                     strobe, white, amber, uv, gamma):
        v = self._common_values(brightness, hue, saturation, value, strobe, white, amber, uv, gamma)
        addr = self.dmx_address
        par_block = [
            v['r_dmx'], v['g_dmx'], v['b_dmx'],
            v['a_dmx'], v['w_dmx'], v['uv_dmx'],
            v['par_strobe_dmx'],
        ]
        universe.set_channels(addr, [
            *par_block,             # Ch  1– 7 PAR 1
            *par_block,             # Ch  8–14 PAR 2
            *par_block,             # Ch 15–21 PAR 3
            *par_block,             # Ch 22–28 PAR 4
            v['dr_dmx'],            # Ch 29 Derby Red
            v['dg_dmx'],            # Ch 30 Derby Green
            v['db_dmx'],            # Ch 31 Derby Blue
            v['dw_dmx'],            # Ch 32 Derby White
            v['derby_strobe_dmx'],  # Ch 33 Derby Strobe
            v['derby_rot_dmx'],     # Ch 34 Derby Rotation
            v['flash_level'],       # Ch 35 Flash LED 1
            v['flash_level'],       # Ch 36 Flash LED 2
            v['flash_level'],       # Ch 37 Flash LED 3
            v['flash_level'],       # Ch 38 Flash LED 4
            v['flash_strobe_dmx'],  # Ch 39 Flash Strobe
            v['laser_color_dmx'],   # Ch 40 Laser Color
            v['laser_pattern_dmx'], # Ch 41 Laser Pattern
            v['spot_pan_dmx'],      # Ch 42 Spot Pan
            0,                      # Ch 43 Spot Fine Pan
            v['spot_tilt_dmx'],     # Ch 44 Spot Tilt
            0,                      # Ch 45 Spot Fine Tilt
            v['spot_speed_dmx'],    # Ch 46 Spot Speed
            self._spot_color_dmx,   # Ch 47 Spot Color Wheel
            0,                      # Ch 48 Spot Gobo (open)
            v['spot_dimmer_dmx'],   # Ch 49 Spot Dimmer
            v['spot_strobe_dmx'],   # Ch 50 Spot Strobe
            0,                      # Ch 51 ILS (off)
        ])

    # ── Diagnostic helper ────────────────────────────────────────────────

    def get_channel_labels(self) -> dict:
        a = self.dmx_address
        if self._personality == "51ch":
            return {
                "PAR 1 Red":    a + CH51_PAR1_RED,
                "PAR 1 Green":  a + CH51_PAR1_GREEN,
                "PAR 1 Blue":   a + CH51_PAR1_BLUE,
                "PAR 2 Red":    a + CH51_PAR2_RED,
                "PAR 3 Red":    a + CH51_PAR3_RED,
                "PAR 4 Red":    a + CH51_PAR4_RED,
                "Derby Rot":    a + CH51_DERBY_ROTATION,
                "Flash 1":      a + CH51_FLASH_1,
                "Laser":        a + CH51_LASER_COLOR,
                "Spot Pan":     a + CH51_SPOT_PAN,
                "Spot Tilt":    a + CH51_SPOT_TILT,
                "Spot Dimmer":  a + CH51_SPOT_DIMMER,
                "Spot Strobe":  a + CH51_SPOT_STROBE,
                "ILS":          a + CH51_ILS,
            }
        return {
            "Par Red":      a + CH29_PAR_RED,
            "Par Green":    a + CH29_PAR_GREEN,
            "Par Blue":     a + CH29_PAR_BLUE,
            "Par Amber":    a + CH29_PAR_AMBER,
            "Par White":    a + CH29_PAR_WHITE,
            "Par UV":       a + CH29_PAR_UV,
            "Par Strobe":   a + CH29_PAR_STROBE,
            "Derby Rot":    a + CH29_DERBY_ROTATION,
            "Flash 1":      a + CH29_FLASH_1,
            "Laser":        a + CH29_LASER_COLOR,
            "Spot Pan":     a + CH29_SPOT_PAN,
            "Spot Tilt":    a + CH29_SPOT_TILT,
            "Spot Dimmer":  a + CH29_SPOT_DIMMER,
            "Spot Strobe":  a + CH29_SPOT_STROBE,
        }


# ── Backward-compatible aliases (29Ch names, used by tests and older code) ──
NUM_CHANNELS      = NUM_CHANNELS_29
CH_PAR_RED        = CH29_PAR_RED
CH_PAR_GREEN      = CH29_PAR_GREEN
CH_PAR_BLUE       = CH29_PAR_BLUE
CH_PAR_AMBER      = CH29_PAR_AMBER
CH_PAR_WHITE      = CH29_PAR_WHITE
CH_PAR_UV         = CH29_PAR_UV
CH_PAR_STROBE     = CH29_PAR_STROBE
CH_DERBY_RED      = CH29_DERBY_RED
CH_DERBY_GREEN    = CH29_DERBY_GREEN
CH_DERBY_BLUE     = CH29_DERBY_BLUE
CH_DERBY_WHITE    = CH29_DERBY_WHITE
CH_DERBY_STROBE   = CH29_DERBY_STROBE
CH_DERBY_ROTATION = CH29_DERBY_ROTATION
CH_FLASH_1        = CH29_FLASH_1
CH_FLASH_2        = CH29_FLASH_2
CH_FLASH_3        = CH29_FLASH_3
CH_FLASH_4        = CH29_FLASH_4
CH_FLASH_STROBE   = CH29_FLASH_STROBE
CH_LASER_COLOR    = CH29_LASER_COLOR
CH_LASER_PATTERN  = CH29_LASER_PATTERN
CH_SPOT_PAN       = CH29_SPOT_PAN
CH_SPOT_PAN_FINE  = CH29_SPOT_PAN_FINE
CH_SPOT_TILT      = CH29_SPOT_TILT
CH_SPOT_TILT_FINE = CH29_SPOT_TILT_FINE
CH_SPOT_SPEED     = CH29_SPOT_SPEED
CH_SPOT_COLOR     = CH29_SPOT_COLOR
CH_SPOT_GOBO      = CH29_SPOT_GOBO
CH_SPOT_DIMMER    = CH29_SPOT_DIMMER
CH_SPOT_STROBE    = CH29_SPOT_STROBE
