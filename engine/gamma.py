"""
Gamma correction for DMX output.

Human vision is not linear — a raw 50% signal looks much brighter than
halfway. Applying a gamma curve (typically 2.2) makes dimming and color
fades look perceptually smooth on LED fixtures.

output = input ** gamma
"""

DEFAULT_GAMMA = 2.2


def apply_gamma(value: float, gamma: float = DEFAULT_GAMMA) -> float:
    """
    Apply gamma correction to a normalized 0.0–1.0 value.

    Returns a corrected 0.0–1.0 value suitable for further scaling.
    Input is clamped before processing.
    """
    value = max(0.0, min(1.0, value))
    return value ** gamma


def apply_gamma_to_dmx(value: float, gamma: float = DEFAULT_GAMMA) -> int:
    """
    Apply gamma correction and scale to DMX 0–255 integer.

    Convenience wrapper for use in fixture mappers.
    """
    corrected = apply_gamma(value, gamma)
    return int(round(corrected * 255))


def apply_gamma_rgb(r: float, g: float, b: float,
                    gamma: float = DEFAULT_GAMMA) -> tuple:
    """
    Apply gamma correction to an RGB triplet (0.0–1.0 each).

    Returns (r, g, b) as 0–255 integers.
    """
    return (
        apply_gamma_to_dmx(r, gamma),
        apply_gamma_to_dmx(g, gamma),
        apply_gamma_to_dmx(b, gamma),
    )
