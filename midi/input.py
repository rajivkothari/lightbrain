"""
MIDI CC input — maps hardware controller knobs/buttons to lighting parameters.

Requires: mido[ports-rtmidi]  (pip install mido[ports-rtmidi])
If mido is not installed, MidiInput degrades gracefully — open() returns False,
get_events() returns [], and is_available() returns False.

Default CC map (generic DJ controller / Behringer BCR2000):
  CC  7  = master dimmer      (0–127 → 0.0–1.0)
  CC 82  = blackout toggle    (value > 64 = on; ≤ 64 = off)
  CC 83  = mode: dinner
  CC 84  = mode: speech
  CC 85  = mode: open_dance
  CC 86  = mode: banger
  CC 87  = mode: indian_latin
  CC 88  = mode: slow_dance

Events are placed in a queue for non-blocking consumption each frame.
"""

import queue
import threading
from dataclasses import dataclass
from typing import Optional


# ---------------------------------------------------------------------------
# Event types
# ---------------------------------------------------------------------------

@dataclass
class MidiEvent:
    """A single MIDI control-change event decoded into LightBrain terms."""
    type: str        # "mode" | "dimmer" | "blackout"
    value: object    # mode key (str) for "mode", float 0–1 for others
    raw_cc: int = 0
    raw_value: int = 0


# ---------------------------------------------------------------------------
# CC map
# ---------------------------------------------------------------------------

CC_MASTER_DIMMER = 7
CC_BLACKOUT      = 82

_CC_MODE_MAP: dict = {
    83: "dinner",
    84: "speech",
    85: "open_dance",
    86: "banger",
    87: "indian_latin",
    88: "slow_dance",
}


# ---------------------------------------------------------------------------
# MidiInput
# ---------------------------------------------------------------------------

def _mido_available() -> bool:
    try:
        import mido  # noqa: F401
        return True
    except ImportError:
        return False


class MidiInput:
    """
    Non-blocking MIDI CC listener.

    Usage:
        midi = MidiInput()
        if midi.open():
            print("MIDI ready")
        # each frame:
        for evt in midi.get_events():
            if evt.type == "mode": ...
        # on shutdown:
        midi.close()
    """

    def __init__(self, port_name: Optional[str] = None):
        self._port_name  = port_name   # None = auto-select first available port
        self._queue: queue.Queue = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._port       = None

    @staticmethod
    def is_available() -> bool:
        """True if the mido library is installed."""
        return _mido_available()

    @staticmethod
    def list_ports() -> list:
        """Return a list of available MIDI input port name strings."""
        if not _mido_available():
            return []
        try:
            import mido
            return mido.get_input_names()
        except Exception:
            return []

    def open(self) -> bool:
        """
        Open a MIDI input port and start the listener thread.
        Returns True on success, False if mido is unavailable or no ports found.
        """
        if not _mido_available():
            return False
        try:
            import mido
            ports = mido.get_input_names()
            if not ports:
                return False
            port_name = self._port_name or ports[0]
            self._port = mido.open_input(port_name)
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._listener_fn, daemon=True, name="midi-input"
            )
            self._thread.start()
            return True
        except Exception:
            return False

    def close(self) -> None:
        """Stop the listener thread and close the MIDI port."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=0.5)
        if self._port:
            try:
                self._port.close()
            except Exception:
                pass
        self._port = None

    def get_events(self) -> list:
        """Drain and return all queued MidiEvents (non-blocking)."""
        events = []
        while not self._queue.empty():
            try:
                events.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return events

    # ------------------------------------------------------------------
    # Internal listener
    # ------------------------------------------------------------------

    def _listener_fn(self) -> None:
        try:
            while not self._stop_event.is_set():
                if self._port is None:
                    break
                for msg in self._port.iter_pending():
                    if msg.type == "control_change":
                        self._handle_cc(msg.control, msg.value)
                # 5 ms poll prevents spin-lock while still staying responsive
                self._stop_event.wait(timeout=0.005)
        except Exception:
            pass

    def _handle_cc(self, cc: int, value: int) -> None:
        if cc == CC_MASTER_DIMMER:
            self._queue.put_nowait(MidiEvent(
                type="dimmer", value=value / 127.0,
                raw_cc=cc, raw_value=value,
            ))
        elif cc == CC_BLACKOUT:
            self._queue.put_nowait(MidiEvent(
                type="blackout", value=1.0 if value > 64 else 0.0,
                raw_cc=cc, raw_value=value,
            ))
        elif cc in _CC_MODE_MAP and value > 0:
            # Only fire on press (value > 0), not button release
            self._queue.put_nowait(MidiEvent(
                type="mode", value=_CC_MODE_MAP[cc],
                raw_cc=cc, raw_value=value,
            ))
