"""
Hybrid sleep/spin-lock for sub-millisecond-accurate frame pacing.

Standard time.sleep() has poor resolution (~15ms on Windows, ~1-4ms on
Linux).  Sleeping to within 2ms of the deadline and busy-spinning the
remainder gives ~50µs accuracy at the cost of ~2ms of CPU per frame
(8% at 40Hz — acceptable for a lighting engine on dedicated hardware).
"""

import time

_SPIN_MARGIN_S: float = 0.002  # busy-spin the last 2ms of each wait


def precise_sleep_until(deadline: float) -> None:
    """
    Block until `deadline` (time.monotonic()) with ~50µs accuracy.

    Uses the hybrid strategy: sleep for most of the remaining interval,
    then busy-spin the last 2ms to avoid OS timer granularity jitter.
    Safe to call even if `deadline` is already in the past (no-op).
    """
    remaining = deadline - time.monotonic()
    if remaining > _SPIN_MARGIN_S:
        time.sleep(remaining - _SPIN_MARGIN_S)
    while time.monotonic() < deadline:
        pass
