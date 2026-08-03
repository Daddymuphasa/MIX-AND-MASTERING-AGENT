"""Human-readable read-outs: what the agent measured, decided, and delivered."""
from __future__ import annotations

from .agent import ProcessingPlan
from .analysis import Analysis
from .mastering import MasterResult


def _fmt(v: float, unit: str = "", nd: int = 1) -> str:
    if v != v:  # NaN
        return "n/a"
    return f"{v:.{nd}f}{unit}"


def analysis_report(a: Analysis) -> str:
    lines = ["── ANALYSIS ─────────────────────────────────────────"]
    lines.append(
        f"  Format        {a.info.codec} "
        f"{a.info.sample_rate/1000:.1f}kHz {a.info.channels}ch"
        + (f" @ {a.info.bit_rate//1000}kbps" if a.info.bit_rate else "")
        + f"  ({a.info.duration:.0f}s)"
    )
    lines.append(f"  Integrated    {_fmt(a.integrated_lufs,' LUFS')}")
    lines.append(f"  True peak     {_fmt(a.true_peak_dbtp,' dBTP',2)}")
    lines.append(f"  Loudness range{_fmt(a.lra,' LU')}")
    if a.crest_db is not None:
        lines.append(f"  Crest factor  {_fmt(a.crest_db,' dB')}")
    if a.codec_cutoff_hz:
        lines.append(f"  Codec ceiling ~{a.codec_cutoff_hz/1000:.1f} kHz (lossy source)")
    if a.relative_bands:
        lines.append("  Tonal balance (dB vs mix):")
        for name, v in a.relative_bands.items():
            bar = _bar(v)
            lines.append(f"     {name:<10}{v:+5.1f}  {bar}")
        lines.append(f"  Tonal tilt    {a.tonal_tilt:+.1f} dB "
                     f"({'bright' if a.tonal_tilt>0 else 'dark'})")
    return "\n".join(lines)


def _bar(v: float, span: float = 12.0, width: int = 20) -> str:
    mid = width // 2
    pos = int(round(v / span * mid))
    pos = max(-mid, min(mid, pos))
    cells = [" "] * width
    cells[mid] = "|"
    if pos >= 0:
        for i in range(mid, mid + pos + 1):
            cells[i] = "█"
    else:
        for i in range(mid + pos, mid + 1):
            cells[i] = "█"
    return "".join(cells)


def plan_report(plan: ProcessingPlan) -> str:
    lines = ["── PLAN ─────────────────────────────────────────────"]
    lines.append(f"  Target        {plan.target.name}  "
                 f"({plan.target.lufs:.0f} LUFS / {plan.target.true_peak:.1f} dBTP)")
    lines.append(f"  EQ chain      {plan.eq_filter or '(none)'}")
    if plan.reasons:
        lines.append("  Reasoning:")
        for r in plan.reasons:
            lines.append(f"    • {r}")
    return "\n".join(lines)


def result_report(results: list[MasterResult]) -> str:
    lines = ["── DELIVERED ────────────────────────────────────────"]
    for r in results:
        lines.append(
            f"  {r.target.name:<10} {_fmt(r.measured_lufs,' LUFS')} / "
            f"{_fmt(r.measured_tp,' dBTP',2)}   {r.path}"
        )
    return "\n".join(lines)
