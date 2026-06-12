"""
DMX output daemon thread.

Decouples pyserial.write() (and any blocking I/O) from the 40Hz DSP loop.

Architecture
------------
Main thread  →  post(universe)  →  _LatestFrame (single-slot buffer)
                                         ↓  ~40Hz
                                   DmxOutputThread._run()
                                         ↓
                                   backend.send(local_universe)

The main thread copies the 512-byte universe snapshot into the buffer in
~1 µs.  The daemon thread wakes at `fps` Hz, grabs the latest snapshot,
and does the actual I/O.

Single-slot "latest-wins" semantics: if the main thread posts two frames
before the DMX thread wakes, only the second one is sent.  This is correct
for DMX — hardware cares about the current state, not a history.

Backend contract
----------------
The `backend` argument must expose:
    send(universe: DMXUniverse) -> None
    close() -> None
    output_type: str   (property or class attribute)
"""

import threading
import time
from typing import Optional

from dmx.universe import DMXUniverse
from engine.pacer import precise_sleep_until

_RECONNECT_THRESHOLD  = 5     # consecutive failures before reconnect attempt
_RECONNECT_BACKOFF_MAX = 30.0  # max seconds between reconnect attempts


class _LatestFrame:
    """
    Single-slot thread-safe frame buffer.

    post() overwrites any previously unread frame.
    take() returns the current frame (or None) and clears the slot.
    """

    __slots__ = ("_data", "_lock")

    def __init__(self) -> None:
        self._data: Optional[bytes] = None
        self._lock = threading.Lock()

    def post(self, data: bytes) -> None:
        with self._lock:
            self._data = data

    def take(self) -> Optional[bytes]:
        with self._lock:
            d, self._data = self._data, None
            return d


class DmxOutputThread:
    """
    Wraps any DMX backend in a dedicated 40Hz daemon thread.

    Usage::

        thread = DmxOutputThread(backend, fps=40)
        thread.start()
        ...
        thread.post(universe)   # called from main thread, non-blocking
        ...
        thread.stop()           # sends blackout, joins, closes backend
    """

    def __init__(self, backend: object, fps: int = 40) -> None:
        self._backend  = backend
        self._frame_t  = 1.0 / max(1, fps)
        self._buf      = _LatestFrame()
        self._stop_evt = threading.Event()
        self._thread: Optional[threading.Thread] = None
        # Health tracking (written by daemon thread, read by main thread — GIL-safe)
        self._consec_failures:   int   = 0
        self._last_error:        str   = ""
        self._reconnect_count:   int   = 0
        self._next_reconnect:    float = 0.0
        self._reconnect_backoff: float = 1.0

    # ------------------------------------------------------------------
    # Main-thread API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the daemon output thread."""
        self._stop_evt.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="lightbrain-dmx"
        )
        self._thread.start()

    def post(self, universe: DMXUniverse) -> None:
        """Copy the universe snapshot into the buffer.  Never blocks."""
        self._buf.post(universe.to_bytes())

    def stop(self) -> None:
        """Signal the thread to stop, send a final blackout, close the backend."""
        self._stop_evt.set()
        if self._thread is not None:
            self._thread.join(timeout=0.5)
        # Caller's thread is safe here — daemon has stopped
        try:
            self._backend.send(DMXUniverse())   # final blackout
        except Exception:
            pass
        try:
            self._backend.close()
        except Exception:
            pass

    @property
    def output_type(self) -> str:
        return getattr(self._backend, "output_type", "UNKNOWN")

    @property
    def health(self) -> dict:
        """Thread-safe health snapshot (reads are atomic under the GIL)."""
        return {
            "ok":              self._consec_failures == 0,
            "error_count":     self._consec_failures,
            "last_error":      self._last_error,
            "reconnect_count": self._reconnect_count,
        }

    # ------------------------------------------------------------------
    # Daemon thread
    # ------------------------------------------------------------------

    def _run(self) -> None:
        local_u  = DMXUniverse()
        deadline = time.monotonic()

        while not self._stop_evt.is_set():
            deadline += self._frame_t

            frame_bytes = self._buf.take()
            if frame_bytes is not None:
                local_u.load_snapshot(frame_bytes)

            try:
                self._backend.send(local_u)
                self._consec_failures  = 0   # success: clear error state
                self._reconnect_backoff = 1.0
            except Exception as _exc:
                self._consec_failures += 1
                self._last_error = str(_exc)[:120]
                # Auto-reconnect after threshold — backoff between attempts
                _now = time.monotonic()
                if (
                    self._consec_failures >= _RECONNECT_THRESHOLD
                    and _now >= self._next_reconnect
                    and hasattr(self._backend, "reopen")
                ):
                    try:
                        self._backend.reopen()
                        self._reconnect_count  += 1
                        self._consec_failures   = 0
                        self._reconnect_backoff = min(
                            self._reconnect_backoff * 2, _RECONNECT_BACKOFF_MAX
                        )
                    except Exception as _re:
                        self._last_error = f"reconnect failed: {_re}"[:120]
                        self._reconnect_backoff = min(
                            self._reconnect_backoff * 2, _RECONNECT_BACKOFF_MAX
                        )
                    self._next_reconnect = _now + self._reconnect_backoff

            # Drift correction: if we're more than one frame behind, resync
            # to avoid a burst of catch-up frames after a stall
            now = time.monotonic()
            if now > deadline + self._frame_t:
                deadline = now
                continue

            precise_sleep_until(deadline)
