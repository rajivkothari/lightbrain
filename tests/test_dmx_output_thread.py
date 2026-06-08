"""
Tests for dmx.output_thread and engine.pacer.

Coverage:
  - _LatestFrame: post/take semantics, latest-wins overwrite, thread safety
  - DmxOutputThread: backend receives frames, stop sends blackout, output_type
  - precise_sleep_until: deadline already past, future deadline, accuracy
"""

import os
import sys
import threading
import time
from typing import List, Optional

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from dmx.universe import DMXUniverse
from dmx.output_thread import _LatestFrame, DmxOutputThread
from engine.pacer import precise_sleep_until


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _RecordingBackend:
    """Test double that records every universe sent to it."""

    output_type = "RECORDING"

    def __init__(self) -> None:
        self.frames: List[bytes] = []
        self.closed = False
        self._lock  = threading.Lock()

    def send(self, universe: DMXUniverse) -> None:
        with self._lock:
            self.frames.append(universe.to_bytes())

    def close(self) -> None:
        self.closed = True

    def last_frame(self) -> Optional[bytes]:
        with self._lock:
            return self.frames[-1] if self.frames else None

    def frame_count(self) -> int:
        with self._lock:
            return len(self.frames)


# ---------------------------------------------------------------------------
# _LatestFrame
# ---------------------------------------------------------------------------

class TestLatestFrame:
    def test_take_empty_returns_none(self):
        buf = _LatestFrame()
        assert buf.take() is None

    def test_post_then_take(self):
        buf = _LatestFrame()
        data = bytes(i % 256 for i in range(512))
        buf.post(data)
        assert buf.take() == data

    def test_take_clears_slot(self):
        buf = _LatestFrame()
        buf.post(b"\xff" * 512)
        buf.take()
        assert buf.take() is None

    def test_latest_wins_overwrite(self):
        buf = _LatestFrame()
        buf.post(b"\x01" * 512)
        buf.post(b"\x02" * 512)   # overwrites without being read
        result = buf.take()
        assert result == b"\x02" * 512
        assert buf.take() is None  # slot cleared after single take

    def test_threaded_post_take(self):
        """Many concurrent posts from multiple threads; take always gets a valid frame."""
        buf    = _LatestFrame()
        errors = []

        def writer(val: int) -> None:
            try:
                for _ in range(100):
                    buf.post(bytes([val] * 512))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        # Final take is either None or a valid 512-byte frame
        result = buf.take()
        assert result is None or len(result) == 512


# ---------------------------------------------------------------------------
# DMXUniverse.load_snapshot
# ---------------------------------------------------------------------------

class TestLoadSnapshot:
    def test_round_trip(self):
        u = DMXUniverse()
        u.set_channel(1, 100)
        u.set_channel(256, 200)
        u.set_channel(512, 42)
        snapshot = u.to_bytes()

        u2 = DMXUniverse()
        u2.load_snapshot(snapshot)
        assert u2.get_channel(1)   == 100
        assert u2.get_channel(256) == 200
        assert u2.get_channel(512) == 42

    def test_load_all_zeros(self):
        u = DMXUniverse()
        u.set_channel(1, 255)
        u.load_snapshot(bytes(512))
        assert u.get_channel(1) == 0

    def test_load_all_ones(self):
        u = DMXUniverse()
        u.load_snapshot(b"\xff" * 512)
        for ch in (1, 100, 256, 512):
            assert u.get_channel(ch) == 255


# ---------------------------------------------------------------------------
# DmxOutputThread
# ---------------------------------------------------------------------------

class TestDmxOutputThread:
    def test_output_type_proxied(self):
        backend = _RecordingBackend()
        thread  = DmxOutputThread(backend, fps=40)
        assert thread.output_type == "RECORDING"

    def test_post_and_receive(self):
        """Thread must deliver a posted frame to the backend within 200ms."""
        backend = _RecordingBackend()
        thread  = DmxOutputThread(backend, fps=40)
        thread.start()

        u = DMXUniverse()
        u.set_channel(1, 77)
        thread.post(u)

        deadline = time.monotonic() + 0.2
        while time.monotonic() < deadline:
            last = backend.last_frame()
            if last is not None and last[0] == 77:
                break
            time.sleep(0.005)
        else:
            pytest.fail("Frame with ch1=77 never reached backend within 200ms")

        thread.stop()

    def test_stop_sends_blackout(self):
        """stop() must send an all-zeros frame as the final output."""
        backend = _RecordingBackend()
        thread  = DmxOutputThread(backend, fps=40)
        thread.start()

        u = DMXUniverse()
        u.set_channel(1, 200)
        thread.post(u)
        time.sleep(0.1)

        thread.stop()

        assert backend.closed
        # The last frame must be all zeros (blackout)
        last = backend.last_frame()
        assert last is not None
        assert all(b == 0 for b in last), "Final frame after stop() must be all-zero blackout"

    def test_stop_closes_backend(self):
        backend = _RecordingBackend()
        thread  = DmxOutputThread(backend, fps=40)
        thread.start()
        thread.stop()
        assert backend.closed

    def test_latest_wins_under_burst(self):
        """Rapid-fire posts: thread must eventually output the last-posted value."""
        backend = _RecordingBackend()
        thread  = DmxOutputThread(backend, fps=40)
        thread.start()

        # Post 50 frames quickly; the last one has ch1=99
        for i in range(50):
            u = DMXUniverse()
            u.set_channel(1, i + 1)
            thread.post(u)

        time.sleep(0.2)
        thread.stop()

        # The backend must have received the value 50 at some point
        received_values = {frame[0] for frame in backend.frames}
        assert 50 in received_values, (
            f"Expected value 50 (last-posted) in received frames, got {received_values}"
        )

    def test_thread_name(self):
        """Daemon thread must carry the expected name for process introspection."""
        backend = _RecordingBackend()
        thread  = DmxOutputThread(backend, fps=40)
        thread.start()
        # Check the internal thread name
        assert thread._thread is not None
        assert thread._thread.name == "lightbrain-dmx"
        thread.stop()

    def test_backend_error_does_not_kill_thread(self):
        """A backend that throws on send() must not crash the daemon thread."""
        class _FaultyBackend:
            output_type = "FAULTY"
            closed = False
            def send(self, u):
                raise RuntimeError("hardware exploded")
            def close(self):
                self.closed = True

        backend = _FaultyBackend()
        thread  = DmxOutputThread(backend, fps=200)
        thread.start()
        time.sleep(0.05)
        # Thread must still be alive
        assert thread._thread is not None and thread._thread.is_alive()
        thread.stop()


# ---------------------------------------------------------------------------
# precise_sleep_until
# ---------------------------------------------------------------------------

class TestPreciseSleepUntil:
    def test_past_deadline_is_noop(self):
        past = time.monotonic() - 1.0
        t0   = time.monotonic()
        precise_sleep_until(past)
        elapsed = time.monotonic() - t0
        assert elapsed < 0.005, f"Past deadline blocked for {elapsed:.3f}s"

    @pytest.mark.skipif(sys.platform == "win32",
                        reason="Windows timer resolution (~15ms) makes sub-3ms accuracy unachievable")
    def test_future_deadline_accuracy(self):
        """Should hit a 25ms deadline within ±3ms (Linux/macOS only)."""
        target_s  = 0.025
        deadline  = time.monotonic() + target_s
        t0        = time.monotonic()
        precise_sleep_until(deadline)
        elapsed   = time.monotonic() - t0
        error_ms  = abs(elapsed - target_s) * 1000
        assert error_ms < 3.0, f"Timing error {error_ms:.2f}ms exceeds 3ms threshold"

    @pytest.mark.skipif(sys.platform == "win32",
                        reason="Windows timer resolution (~15ms) makes sub-3ms accuracy unachievable")
    def test_does_not_overshoot_by_much(self):
        """Never wakes more than 3ms late (Linux/macOS only)."""
        deadline = time.monotonic() + 0.010
        precise_sleep_until(deadline)
        late_ms  = (time.monotonic() - deadline) * 1000
        assert late_ms < 3.0, f"Overshot deadline by {late_ms:.2f}ms"
