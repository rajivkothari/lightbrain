"""
Audio input capture using sounddevice.

Provides a non-blocking ring-buffer-based audio capture that the analyzer
can pull frames from without stalling the main render loop.
"""

import queue
import threading
from typing import Optional, Callable

import numpy as np
import sounddevice as sd


class AudioCapture:
    """
    Non-blocking audio capture from a sounddevice input device.

    Audio frames are pushed into an internal queue by the sounddevice
    callback and consumed by the main thread via get_latest_block().
    """

    def __init__(
        self,
        device_index: Optional[int] = None,
        sample_rate: int = 44100,
        block_size: int = 1024,
        channels: int = 1,
        queue_maxsize: int = 8,
    ):
        self.device_index = device_index
        self.sample_rate  = sample_rate
        self.block_size   = block_size
        self.channels     = channels

        self._queue: queue.Queue = queue.Queue(maxsize=queue_maxsize)
        self._stream: Optional[sd.InputStream] = None
        self._running: bool = False
        self._error: Optional[str] = None
        self._device_name: str = ""

    def start(self) -> None:
        """Open and start the audio input stream."""
        try:
            info = sd.query_devices(self.device_index, "input")
            self._device_name = info["name"]
        except Exception:
            self._device_name = f"device {self.device_index}"

        # Create and start stream before setting _running so that
        # is_running() stays False if stream creation raises.
        stream = sd.InputStream(
            device=self.device_index,
            samplerate=self.sample_rate,
            blocksize=self.block_size,
            channels=self.channels,
            dtype="float32",
            callback=self._callback,
        )
        stream.start()
        # Only mark running after stream is confirmed started
        self._stream  = stream
        self._running = True

    def stop(self) -> None:
        """Stop and close the audio input stream."""
        self._running = False
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def _callback(self, indata: np.ndarray, frames: int,
                  time_info, status) -> None:
        """sounddevice callback — called on the audio thread."""
        if status:
            self._error = str(status)
        # Keep only the most recent block — drop old frames if queue is full
        try:
            self._queue.put_nowait(indata.copy())
        except queue.Full:
            try:
                self._queue.get_nowait()  # discard oldest
                self._queue.put_nowait(indata.copy())
            except queue.Empty:
                pass

    def get_latest_block(self) -> Optional[np.ndarray]:
        """
        Return the most recent audio block, or None if none is available.

        Drains any stale blocks so only the freshest data is returned.
        """
        block = None
        while True:
            try:
                block = self._queue.get_nowait()
            except queue.Empty:
                break
        return block

    @property
    def device_name(self) -> str:
        return self._device_name

    @property
    def last_error(self) -> Optional[str]:
        return self._error

    def is_running(self) -> bool:
        return self._running and self._stream is not None


def list_input_devices() -> list:
    """Return a list of available input devices as dicts."""
    devices = []
    for i, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] > 0:
            devices.append({
                "index": i,
                "name": dev["name"],
                "channels": dev["max_input_channels"],
                "sample_rate": dev["default_samplerate"],
            })
    return devices
