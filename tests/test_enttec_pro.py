"""
Regression tests for dmx.output_enttec_pro.

Validates:
  1. Packet structure: the Enttec Pro / DMXking frame bytes are exactly correct
     (0x7E header, label 0x06, little-endian length, DMX start code, 0xE7 tail).
  2. VCP serial parameters: serial.Serial() is called with the right kwargs —
     no flow control (rtscts/dsrdtr/xonxoff all False), write_timeout set, and
     the non-standard 250 kbaud rate preserved.
  3. PermissionError on open() produces an actionable fix message.
  4. FTDI latency_timer is tuned to 1ms on Linux after port opens.

These tests mock serial.Serial so no hardware is required.
"""

import os
import sys
import tempfile
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
    _tune_latency_timer,
    _LATENCY_TIMER_TARGET,
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


# ---------------------------------------------------------------------------
# EnttecProOutput.open — PermissionError handling
# ---------------------------------------------------------------------------

class TestEnttecProPermissionError:
    """Opening a port without dialout group must give an actionable message."""

    def test_permission_error_message_mentions_dialout(self):
        with mock.patch("dmx.output_enttec_pro.serial.Serial",
                        side_effect=PermissionError("mock")):
            out = EnttecProOutput()
            with pytest.raises(PermissionError, match="dialout"):
                out.open("/dev/ttyUSB0")

    def test_permission_error_message_mentions_usermod(self):
        with mock.patch("dmx.output_enttec_pro.serial.Serial",
                        side_effect=PermissionError("mock")):
            out = EnttecProOutput()
            with pytest.raises(PermissionError, match="usermod"):
                out.open("/dev/ttyUSB0")

    def test_permission_error_warns_against_sudo(self):
        with mock.patch("dmx.output_enttec_pro.serial.Serial",
                        side_effect=PermissionError("mock")):
            out = EnttecProOutput()
            with pytest.raises(PermissionError, match="Do NOT run.*sudo"):
                out.open("/dev/ttyUSB0")

    def test_serial_exception_passes_through(self):
        """Non-permission serial errors must not be wrapped or swallowed."""
        import serial as _serial
        with mock.patch("dmx.output_enttec_pro.serial.Serial",
                        side_effect=_serial.SerialException("no device")):
            out = EnttecProOutput()
            with pytest.raises(_serial.SerialException):
                out.open("/dev/ttyUSB0")


# ---------------------------------------------------------------------------
# _tune_latency_timer — FTDI sysfs tuning
# ---------------------------------------------------------------------------

class TestTuneLatencyTimer:
    """Validate the latency_timer tuning logic against a synthetic sysfs tree."""

    def _make_sysfs(self, tmpdir: str, tty_name: str, value: int) -> str:
        """Create a fake /sys/class/tty/<tty>/device/latency_timer file."""
        device_dir = os.path.join(tmpdir, "sys", "class", "tty",
                                  tty_name, "device")
        os.makedirs(device_dir, exist_ok=True)
        timer_path = os.path.join(device_dir, "latency_timer")
        with open(timer_path, "w") as f:
            f.write(str(value))
        return timer_path

    def test_tunes_16_to_1(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            timer_path = self._make_sysfs(tmpdir, "ttyUSB0", 16)
            real_path = "/sys/class/tty/ttyUSB0/device/latency_timer"
            _real_open = open  # capture before mock replaces it

            with mock.patch("dmx.output_enttec_pro.os.path.basename",
                            return_value="ttyUSB0"):
                with mock.patch("dmx.output_enttec_pro.os.path.exists",
                                side_effect=lambda p: p == real_path):
                    with mock.patch("builtins.open", side_effect=lambda p, *a, **kw:
                                    _real_open(timer_path, *a, **kw) if p == real_path
                                    else _real_open(p, *a, **kw)):
                        _tune_latency_timer("/dev/ttyUSB0")

            with _real_open(timer_path, "r") as f:
                assert f.read().strip() == str(_LATENCY_TIMER_TARGET)

    def test_skips_if_already_low(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            timer_path = self._make_sysfs(tmpdir, "ttyUSB0", 1)
            real_path = "/sys/class/tty/ttyUSB0/device/latency_timer"
            _real_open = open

            with mock.patch("dmx.output_enttec_pro.os.path.basename",
                            return_value="ttyUSB0"):
                with mock.patch("dmx.output_enttec_pro.os.path.exists",
                                side_effect=lambda p: p == real_path):
                    with mock.patch("builtins.open", side_effect=lambda p, *a, **kw:
                                    _real_open(timer_path, *a, **kw) if p == real_path
                                    else _real_open(p, *a, **kw)):
                        _tune_latency_timer("/dev/ttyUSB0")
            # Should remain 1 — no write attempted
            with _real_open(timer_path, "r") as f:
                assert f.read().strip() == "1"

    def test_skips_non_ftdi_device(self):
        """Non-FTDI port (no sysfs entry) must not raise."""
        _tune_latency_timer("/dev/ttyACM0")  # no sysfs → os.path.exists returns False

    def test_permission_denied_does_not_raise(self):
        """If sysfs write fails due to permissions, log a warning but don't crash."""
        real_path = "/sys/class/tty/ttyUSB0/device/latency_timer"
        with mock.patch("dmx.output_enttec_pro.os.path.basename",
                        return_value="ttyUSB0"):
            with mock.patch("dmx.output_enttec_pro.os.path.exists",
                            side_effect=lambda p: p == real_path):
                # read returns 16, write raises PermissionError
                mock_file_read = mock.mock_open(read_data="16")
                mock_file_write = mock.MagicMock(side_effect=PermissionError("no root"))

                def mock_open_fn(p, *a, **kw):
                    if p != real_path:
                        return open(p, *a, **kw)
                    mode = a[0] if a else kw.get("mode", "r")
                    if "w" in mode:
                        raise PermissionError("no root")
                    return mock_file_read(p, *a, **kw)

                with mock.patch("builtins.open", side_effect=mock_open_fn):
                    _tune_latency_timer("/dev/ttyUSB0")  # must not raise

    def test_latency_target_constant_is_1ms(self):
        assert _LATENCY_TIMER_TARGET == 1
