"""Render a master: EQ chain -> loudness/peak targeting.

Two engines, matching the workflow proven on 'Chaos thunder':
  - linear (streaming): two-pass loudnorm, transparent, hits LUFS + true peak.
  - loud: makeup gain into a true-peak-safe limiter (alimiter). Content-limited,
    so a very hot source may settle a little below target — that's intended
    (clean beats loud).
"""
from __future__ import annotations

from dataclasses import dataclass
from math import pow

from . import ffmpeg_utils as ff
from .analysis import _measure_loudness
from .presets import LoudnessTarget


@dataclass
class MasterResult:
    path: str
    target: LoudnessTarget
    measured_lufs: float
    measured_tp: float
    measured_lra: float


def _dbtp_to_linear(db: float) -> float:
    return pow(10.0, db / 20.0)


def _wav_args(bit_depth: int) -> list[str]:
    fmt = {16: "pcm_s16le", 24: "pcm_s24le", 32: "pcm_s32le"}.get(bit_depth, "pcm_s24le")
    return ["-c:a", fmt]


def render(
    src: str,
    out: str,
    eq_filter: str,
    target: LoudnessTarget,
    *,
    sample_rate: int | None = None,
    bit_depth: int = 24,
    verify: bool = True,
) -> MasterResult:
    if target.mode == "linear":
        _render_linear(src, out, eq_filter, target, sample_rate, bit_depth)
    else:
        _render_loud(src, out, eq_filter, target, sample_rate, bit_depth)

    if verify:
        m = _measure_loudness(out)
        return MasterResult(
            out, target,
            m.get("input_i", float("nan")),
            m.get("input_tp", float("nan")),
            m.get("input_lra", float("nan")),
        )
    return MasterResult(out, target, float("nan"), float("nan"), float("nan"))


def _sr_args(sample_rate: int | None) -> list[str]:
    return ["-ar", str(sample_rate)] if sample_rate else []


def _render_linear(
    src: str, out: str, eq: str, target: LoudnessTarget,
    sample_rate: int | None, bit_depth: int,
) -> None:
    base = f"{eq}," if eq else ""

    # Pass 1: measure the EQ'd signal.
    pass1 = (
        f"{base}loudnorm=I={target.lufs}:TP={target.true_peak}:LRA=11:"
        "print_format=json"
    )
    stats = ff.parse_loudnorm_json(ff.measure_filter(src, pass1))

    # Pass 2: apply with measured values (linear = transparent gain, no pumping).
    pass2 = (
        f"{base}loudnorm=I={target.lufs}:TP={target.true_peak}:LRA=11:"
        f"measured_I={stats['input_i']}:measured_TP={stats['input_tp']}:"
        f"measured_LRA={stats['input_lra']}:measured_thresh={stats['input_thresh']}:"
        f"offset={stats.get('target_offset', 0.0)}:linear=true:print_format=summary"
    )
    args = [
        ff.ffmpeg(), "-hide_banner", "-y", "-i", src,
        "-map", "a:0", "-af", pass2,
        *_sr_args(sample_rate), *_wav_args(bit_depth), out,
    ]
    ff.run(args)


def _render_loud(
    src: str, out: str, eq: str, target: LoudnessTarget,
    sample_rate: int | None, bit_depth: int,
) -> None:
    base = f"{eq}," if eq else ""

    # Measure EQ'd loudness so we know how much gain to add before the limiter.
    measure = f"{base}loudnorm=print_format=json"
    stats = ff.parse_loudnorm_json(ff.measure_filter(src, measure))
    gain_db = target.lufs - stats["input_i"]
    gain_db = max(-24.0, min(18.0, gain_db))  # sanity clamp

    limit = _dbtp_to_linear(target.true_peak)
    chain = (
        f"{base}volume={gain_db:.2f}dB,"
        f"alimiter=limit={limit:.4f}:attack=5:release=50:level=false:asc=1"
    )
    args = [
        ff.ffmpeg(), "-hide_banner", "-y", "-i", src,
        "-map", "a:0", "-af", chain,
        *_sr_args(sample_rate), *_wav_args(bit_depth), out,
    ]
    ff.run(args)
