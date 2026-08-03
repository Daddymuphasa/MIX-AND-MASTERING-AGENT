"""Distribution-ready exports.

Resamples with the high-quality soxr resampler and writes the delivery formats
the user asked for: MP3 320 kbps CBR and 24-bit FLAC, both at 44.1 kHz, plus WAV.
"""
from __future__ import annotations

import os

from . import ffmpeg_utils as ff

# format id -> (extension, encoder args builder)
_RATE = 44_100


def _soxr(rate: int, dither: bool) -> str:
    graph = f"aresample=resampler=soxr:precision=28:out_sample_rate={rate}"
    if dither:
        graph += ":dither_method=triangular"
    return graph


def export(src: str, out_dir: str, stem: str, formats: list[str], *, rate: int = _RATE) -> list[str]:
    os.makedirs(out_dir, exist_ok=True)
    written: list[str] = []
    for fmt in formats:
        written.append(_export_one(src, out_dir, stem, fmt, rate))
    return written


def _export_one(src: str, out_dir: str, stem: str, fmt: str, rate: int) -> str:
    ffmpeg = ff.ffmpeg()

    if fmt in {"mp3", "mp3320"}:
        out = os.path.join(out_dir, f"{stem}.mp3")
        args = [
            ffmpeg, "-hide_banner", "-y", "-i", src,
            "-af", _soxr(rate, dither=True),
            "-c:a", "libmp3lame", "-b:a", "320k", out,
        ]
    elif fmt in {"flac", "flac24"}:
        out = os.path.join(out_dir, f"{stem}.flac")
        args = [
            ffmpeg, "-hide_banner", "-y", "-i", src,
            "-af", _soxr(rate, dither=False),
            "-c:a", "flac", "-sample_fmt", "s32", out,  # s32 -> 24-bit FLAC
        ]
    elif fmt == "flac16":
        out = os.path.join(out_dir, f"{stem}.flac")
        args = [
            ffmpeg, "-hide_banner", "-y", "-i", src,
            "-af", _soxr(rate, dither=True),
            "-c:a", "flac", "-sample_fmt", "s16", out,
        ]
    elif fmt in {"wav", "wav24"}:
        out = os.path.join(out_dir, f"{stem}.wav")
        args = [
            ffmpeg, "-hide_banner", "-y", "-i", src,
            "-af", _soxr(rate, dither=False),
            "-c:a", "pcm_s24le", out,
        ]
    else:
        raise ValueError(f"Unknown export format: {fmt}")

    ff.run(args)
    return out
