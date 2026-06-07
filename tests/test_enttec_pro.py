"""
Regression tests for dmx.output_enttec_pro.

Validates two things:
  1. Packet structure: the Enttec Pro / DMXking frame bytes are exactly correct
     (0x7E header, label 0x06, little-endian length, DMX start code, 0xE7 tail).
  2. VCP serial parameters: serial.Serial() is called with the right kwargs —
     no flow control (rtscts/dsrdtr/xonxoff all False), write_timeout set, and
     the non-standard 250 kbaud rate preserved.

These tests mock serial.Serial so no hardware is required.
"""

import os
import sys
import unittest.mock as mock

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from dmx.universe import DMXUniverse
from dmx.output_enttec_pro import (
    build_enttec_frame,
    EnttecProOutput,
    ENTTEC_PRO_START,
    ENTTEC_PRO_END,
    ENTTEC_PRO_LABEL,
)


# ---------------------------------------------------------------------------
# build_enttec_frame — packet structure
# ---------------------------------------------------------------------------

class TestBuildEnttecFrame:
    """The DMXking uses this exact wire format; do not change it."""

    def test_start_byte(self):
        frame = build_enttec_frame(DMXUniverse())
        assert frame[0] == 0x7E, "Frame must begin with 0x7E (Enttec start of message)"

    def test_label_byte(self):
        frame = build_enttec_frame(DMXUniverse())
        assert frame[1] == 0x06, "Label must be 0x06 (Output Only Send DMX Packet Request)"

    def test_end_byte(self):
        frame = build_enttec_frame(DMXUniverse())
        assert frame[-1] == 0xE7, "Frame must end with 0xE7 (Enttec end of message)"

    def test_length_field_little_endian(self):
        # data = DMX start code (1 byte) + 512 channel bytes = 513 bytes
        frame = build_enttec_frame(DMXUniverse())
        length_lo = frame[2]
        length_hi = frame[3]
        decoded   = length_lo | (length_hi << 8)
        assert decoded == 513, f"Length field should be 513 (start code + 512 ch), got {decoded}"

    def test_dmx_start_code_is_zero(self):
        frame = build_enttec_frame(DMXUniverse())
        assert frame[4] == 0x00, "DMX start code (byte 4) must be 0x00"

    def test_total_frame_length(self):
        # 0x7E + label + len_lo + len_hi + 0x00 + 512 ch + 0xE7 = 518 bytes
        frame = build_enttec_frame(DMXUniverse())
        assert len(frame) == 518, f"Expected 518-byte frame, got {len(frame)}"

    def test_channel_data_position(self):
        """Channel data starts at byte 5 (after header + length + start code)."""
        u = DMXUniverse()
        u.set_channel(1,   0xAA)
        u.set_channel(2,   0xBB)
        u.set_channel(512, 0xFF)
        frame = build_enttec_frame(u)
        assert frame[5]   == 0xAA, "Channel 1 must be at frame byte 5"
        assert frame[6]   == 0xBB, "Channel 2 must be at frame byte 6"
        assert frame[516] == 0xFF, "Channel 512 must be at frame byte 516"

    def test_blackout_universe_produces_zeros(self):
        u = DMXUniverse()
        frame = build_enttec_frame(u)
        channel_bytes = frame[5:517]  # 512 DMX channel bytes
        assert all(b == 0 for b in channel_bytes), "Blank universe must produce all-zero channel bytes"

    def test_full_bright_universe(self):
        u = DMXUniverse()
        for ch in range(1, 513):
            u.set_channel(ch, 255)
        frame = build_enttec_frame(u)
        channel_bytes = frame[5:517]
        assert all(b == 255 for b in channel_bytes), "Full-bright universe must produce all 0xFF channel bytes"

    def test_constants_match_spec(self):
        assert ENTTEC_PRO_START == 0x7E
        assert ENTTEC_PRO_END   == 0xE7
        assert ENTTEC_PRO_LABEL == 0x06


# ---------------------------------------------------------------------------
# EnttecProOutput.open — VCP serial parameters
# ---------------------------------------------------------------------------

class TestEnttecProOutputOpen:
    """Verify serial.Serial() is configured for pure VCP, no flow control."""

    def _open_with_mock(self, port: str = "/dev/ttyUSB0",
                        baud: int = 250000) -> mock.MagicMock:
        """Call open() with a mocked serial.Serial; return the mock call kwargs."""
        with mock.patch("dmx.output_enttec_pro.serial.Serial") as mock_serial:
            mock_instance = mock.MagicMock()
            mock_instance.is_open = True
            mock_serial.return_value = mock_instance
            out = EnttecProOutput()
            out.open(port, baud)
        return mock_serial.call_args

    def test_baud_rate_default(self):
        call = self._open_with_mock()
        assert call.kwargs.get("baudrate") == 250000, "Default baud must be 250000 (Enttec spec)"

    def test_no_rtscts(self):
        call = self._open_with_mock()
        assert call.kwargs.get("rtscts") is False, "rtscts must be False — no hardware flow control"

    def test_no_dsrdtr(self):
        call = self._open_with_mock()
        assert call.kwargs.get("dsrdtr") is False, "dsrdtr must be False — no hardware flow control"

    def test_no_xonxoff(self):
        call = self._open_with_mock()
        assert call.kwargs.get("xonxoff") is False, "xonxoff must be False — no software flow control"

    def test_write_timeout_set(self):
        call = self._open_with_mock()
        wt = call.kwargs.get("write_timeout")
        assert wt is not None, "write_timeout must be set to prevent indefinite stalls"
        assert 0 < wt <= 1.0, f"write_timeout should be a short positive value, got {wt}"

    def test_eight_data_bits(self):
        import serial
        call = self._open_with_mock()
        assert call.kwargs.get("bytesize") == serial.EIGHTBITS

    def test_no_parity(self):
        import serial
        call = self._open_with_mock()
        assert call.kwargs.get("parity") == serial.PARITY_NONE

    def test_two_stop_bits(self):
        """DMX512 protocol requires 2 stop bits — must not be changed."""
        import serial
        call = self._open_with_mock()
        assert call.kwargs.get("stopbits") == serial.STOPBITS_TWO, \
            "DMX512 requires 2 stop bits — do not change this"

    def test_port_stored(self):
        with mock.patch("dmx.output_enttec_pro.serial.Serial") as mock_serial:
            mock_serial.return_value.is_open = True
            out = EnttecProOutput()
            out.open("/dev/ttyUSB0")
        assert out.port == "/dev/ttyUSB0"

    def test_custom_baud_passed_through(self):
        call = self._open_with_mock(baud=115200)
        assert call.kwargs.get("baudrate") == 115200
