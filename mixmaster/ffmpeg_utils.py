"""Thin wrappers around ffmpeg / ffprobe.

Everything in this project is driven by ffmpeg (the DSP engine). These helpers
run it safely (argument lists, never a shell string) and parse the bits of its
stderr/JSON output we care about.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass


class FFmpegNotFound(RuntimeError):
    pass


def _require(tool: str) -> str:
    path = shutil.which(tool)
    if not path:
        raise FFmpegNotFound(
            f"'{tool}' was not found on PATH. Install ffmpeg (which bundles {tool}) "
            "and make sure it is on your PATH."
        )
    return path


def run(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    """Run a command, capturing stdout+stderr as text. ffmpeg writes progress and
    filter metadata to stderr, so we always capture it."""
    proc = subprocess.run(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"Command failed ({proc.returncode}): {' '.join(args[:3])} ...\n"
            f"{proc.stderr[-2000:]}"
        )
    return proc


def ffmpeg() -> str:
    return _require("ffmpeg")


def ffprobe() -> str:
    return _require("ffprobe")


@dataclass
class MediaInfo:
    codec: str
    sample_rate: int
    channels: int
    duration: float
    bit_rate: int | None
    format_name: str

    @property
    def is_lossy(self) -> bool:
        return self.codec in {"mp3", "aac", "vorbis", "opus", "wmav2"}


def probe(path: str) -> MediaInfo:
    """Return basic audio stream info via ffprobe."""
    args = [
        ffprobe(),
        "-v",
        "error",
        "-show_format",
        "-show_streams",
        "-select_streams",
        "a:0",
        "-of",
        "json",
        path,
    ]
    out = run(args).stdout
    data = json.loads(out)
    stream = (data.get("streams") or [{}])[0]
    fmt = data.get("format") or {}
    br = stream.get("bit_rate") or fmt.get("bit_rate")
    return MediaInfo(
        codec=stream.get("codec_name", "unknown"),
        sample_rate=int(stream.get("sample_rate", 0) or 0),
        channels=int(stream.get("channels", 0) or 0),
        duration=float(fmt.get("duration", 0) or stream.get("duration", 0) or 0),
        bit_rate=int(br) if br else None,
        format_name=fmt.get("format_name", stream.get("codec_name", "unknown")),
    )


def measure_filter(path: str, filtergraph: str) -> str:
    """Apply an audio filtergraph and decode to null, returning the full stderr
    (used to scrape astats / loudnorm / ebur128 metadata)."""
    args = [
        ffmpeg(),
        "-hide_banner",
        "-nostats",
        "-i",
        path,
        "-map",
        "a:0",
        "-af",
        filtergraph,
        "-f",
        "null",
        "-",
    ]
    return run(args).stderr


# ---- parsing helpers -------------------------------------------------------

_LOUDNORM_JSON = re.compile(r"\{[^{}]*\"input_i\"[^{}]*\}", re.DOTALL)


def parse_loudnorm_json(stderr: str) -> dict[str, float]:
    """Extract the JSON block printed by loudnorm=print_format=json."""
    m = _LOUDNORM_JSON.search(stderr)
    if not m:
        raise ValueError("Could not find loudnorm JSON in ffmpeg output.")
    raw = json.loads(m.group(0))
    out: dict[str, float] = {}
    for k, v in raw.items():
        try:
            out[k] = float(v)
        except (TypeError, ValueError):
            pass
    return out


def _last_float(stderr: str, label: str) -> float | None:
    """Return the last 'label: <number>' value in astats output (the Overall
    block is printed last, so the last hit is the overall figure)."""
    matches = re.findall(rf"{re.escape(label)}:\s*(-?\d+(?:\.\d+)?)", stderr)
    if not matches:
        return None
    return float(matches[-1])


def parse_astats(stderr: str) -> dict[str, float | None]:
    return {
        "rms_db": _last_float(stderr, "RMS level dB"),
        "peak_db": _last_float(stderr, "Peak level dB"),
        "crest_factor": _last_float(stderr, "Crest factor"),
    }
