"""
Audio file loader for Song Preview Mode.

Loads WAV / FLAC / OGG / MP3 files into a mono float32 numpy array suitable
for offline analysis.

Backend priority:
  1. soundfile  — WAV, FLAC, OGG (fast, C library, no ffmpeg)
  2. scipy.io.wavfile — WAV only (always available with scipy)
  3. ImportError with install hint if neither works

MP3 support requires soundfile compiled with MPEG support or an optional
`pydub` + ffmpeg path (not in base requirements).

Exported functions:
  load_audio_file(path, target_sr=44100) -> Tuple[np.ndarray, int]
"""

import os
import numpy as np
from typing import Tuple


def load_audio_file(
    path: str,
    target_sr: int = 44100,
) -> Tuple[np.ndarray, int]:
    """
    Load an audio file and return (mono_float32_array, sample_rate).

    The array is normalized so values are in approximately [-1.0, 1.0].
    Multi-channel files are down-mixed to mono by averaging channels.

    path      — path to audio file (WAV, FLAC, OGG, MP3 if supported)
    target_sr — desired sample rate; the raw file rate is returned if
                resampling is not available (offline analyzer handles this)

    Raises:
      FileNotFoundError if the file does not exist
      ImportError if no audio backend is available
      ValueError if the file cannot be decoded
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"Audio file not found: {path}")

    ext = os.path.splitext(path)[1].lower()

    # --- Try soundfile (WAV, FLAC, OGG, and some MP3) ---
    try:
        import soundfile as sf
        data, sr = sf.read(path, dtype="float32", always_2d=False)
        if data.ndim > 1:
            data = data.mean(axis=1)
        return data, sr
    except ImportError:
        pass
    except Exception as e:
        raise ValueError(f"soundfile could not load {path}: {e}") from e

    # --- Fall back to scipy for WAV files ---
    if ext in (".wav",):
        try:
            from scipy.io import wavfile
            sr, data = wavfile.read(path)
            data = _normalize_scipy(data)
            return data, sr
        except ImportError:
            pass
        except Exception as e:
            raise ValueError(f"scipy.io.wavfile could not load {path}: {e}") from e

    raise ImportError(
        "No audio backend available. Install soundfile:\n"
        "  pip install soundfile\n"
        "For MP3 support: pip install pydub  (also requires ffmpeg)"
    )


def _normalize_scipy(data: np.ndarray) -> np.ndarray:
    """Normalize scipy wavfile integer arrays to float32 in [-1.0, 1.0]."""
    if data.dtype == np.int16:
        data = data.astype(np.float32) / 32768.0
    elif data.dtype == np.int32:
        data = data.astype(np.float32) / 2147483648.0
    elif data.dtype == np.uint8:
        data = (data.astype(np.float32) - 128.0) / 128.0
    else:
        data = data.astype(np.float32)

    if data.ndim > 1:
        data = data.mean(axis=1)
    return data
