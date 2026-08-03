"""Learn a mixing pattern from a before/after pair.

Give it an *unmixed* (dry) file and a *mixed* version of the same performance.
It loudness-matches them, then compares their average spectra to recover the EQ
move that turns dry -> mixed, plus how much the stereo width and dynamics changed.
The result is saved as a reusable LearnedProfile you can apply to new tracks.

The cleaner the pairing (same performance, one processed), the sharper the match —
so the ideal input is a dry vocal stem and its mixed counterpart.
"""
from __future__ import annotations

import os
import tempfile

import numpy as np

from . import ffmpeg_utils as ff
from .profiles import LearnedProfile

# Frequencies we sample the match curve at (log-spaced, musical).
_CURVE_HZ = [
    30, 45, 60, 90, 120, 160, 220, 300, 420, 600, 850, 1200,
    1700, 2400, 3200, 4500, 6000, 8000, 11000, 14000, 17000,
]


def _decode(path: str, rate: int = 44_100) -> tuple[np.ndarray, int]:
    """Decode anything (mp3/wav/flac/...) to a float32 stereo array via ffmpeg,
    so we don't depend on libsndfile codec support."""
    import soundfile as sf

    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    try:
        ff.run([
            ff.ffmpeg(), "-hide_banner", "-y", "-i", path,
            "-ac", "2", "-ar", str(rate), "-c:a", "pcm_s24le", tmp.name,
        ])
        data, sr = sf.read(tmp.name, always_2d=True)
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
    return data.astype(np.float64), sr


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(x)) + 1e-12))


def _avg_spectrum(mono: np.ndarray, sr: int) -> tuple[np.ndarray, np.ndarray]:
    from scipy.signal import welch

    nper = 8192
    freqs, pxx = welch(mono, fs=sr, nperseg=nper, noverlap=nper // 2, detrend=False)
    return freqs, pxx


def _smooth_log(freqs: np.ndarray, curve_db: np.ndarray, octaves: float = 1 / 3) -> np.ndarray:
    """Smooth a per-bin curve over a fractional-octave window (log frequency)."""
    out = np.empty_like(curve_db)
    logf = np.log2(np.maximum(freqs, 1e-6))
    for i in range(len(freqs)):
        lo, hi = logf[i] - octaves, logf[i] + octaves
        mask = (logf >= lo) & (logf <= hi)
        out[i] = curve_db[mask].mean() if mask.any() else curve_db[i]
    return out


def _width(stereo: np.ndarray) -> float:
    if stereo.shape[1] < 2:
        return 0.0
    mid = (stereo[:, 0] + stereo[:, 1]) / 2
    side = (stereo[:, 0] - stereo[:, 1]) / 2
    return _rms(side) / (_rms(mid) + 1e-9)


def _crest_db(mono: np.ndarray) -> float:
    peak = float(np.max(np.abs(mono)) + 1e-9)
    rms = _rms(mono)
    return 20 * np.log10(peak / rms)


def learn(before: str, after: str, name: str, *, smoothing: float = 1 / 3) -> LearnedProfile:
    b, sr = _decode(before)
    a, _ = _decode(after)

    b_mono = b.mean(axis=1)
    a_mono = a.mean(axis=1)

    # Loudness-match on RMS so the curve reflects tone, not level.
    level_db = 20 * np.log10(_rms(a_mono) / (_rms(b_mono) + 1e-12))
    scale = _rms(b_mono) / (_rms(a_mono) + 1e-12)
    a_mono_m = a_mono * scale

    fb, pb = _avg_spectrum(b_mono, sr)
    fa, pa = _avg_spectrum(a_mono_m, sr)

    diff_db = 10 * np.log10((pa + 1e-12) / (pb + 1e-12))
    diff_db = _smooth_log(fa, diff_db, smoothing)

    # Sample the smoothed match curve at musical frequencies, clamp to sane range.
    curve: list[tuple[float, float]] = []
    for hz in _CURVE_HZ:
        if hz >= sr / 2:
            break
        g = float(np.interp(hz, fa, diff_db))
        curve.append((float(hz), float(np.clip(g, -12.0, 12.0))))

    # Detrend the curve so it's a pure tonal *shape* (mean move removed).
    mean_g = np.mean([g for _, g in curve])
    curve = [(f, round(g - mean_g, 2)) for f, g in curve]

    width_before = _width(b)
    width_after = _width(a)
    width_factor = float(np.clip(
        (width_after + 0.02) / (width_before + 0.02), 0.5, 2.0
    ))

    crest_before = _crest_db(b_mono)
    crest_after = _crest_db(a_mono_m)
    # More compression in 'after' -> lower crest. Map the drop to a gentle ratio.
    crest_drop = max(0.0, crest_before - crest_after)
    dynamics_ratio = float(np.clip(1.0 + crest_drop / 6.0, 1.0, 4.0))

    return LearnedProfile(
        name=name,
        eq_curve=curve,
        width_factor=round(width_factor, 3),
        dynamics_ratio=round(dynamics_ratio, 2),
        level_db=round(float(level_db), 2),
        meta={
            "before": os.path.basename(before),
            "after": os.path.basename(after),
            "sr": sr,
            "crest_before_db": round(crest_before, 2),
            "crest_after_db": round(crest_after, 2),
            "width_before": round(width_before, 3),
            "width_after": round(width_after, 3),
            "smoothing_oct": smoothing,
        },
    )
