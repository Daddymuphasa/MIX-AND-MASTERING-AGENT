"""8D / spatial orbit.

Reproduces the effect dialed in on 'Chaos thunder': the low end (kick + bass)
stays mono and locked to the centre, while the upper band is mono-summed and
orbited around the head. It can run across the whole track or be gated to
specific time windows with short ramps so there are no clicks at the edges.

Best on headphones — on a mono/phone speaker it collapses back to near-normal,
which is expected.
"""
from __future__ import annotations

import os
import tempfile

from . import ffmpeg_utils as ff
from . import mastering
from .presets import TARGETS, LoudnessTarget

# intensity -> autopan depth (0..1). 0.25 ~= a few dB of L/R swing (mild),
# 1.0 = hard left-to-right.
INTENSITY = {"mild": 0.25, "moderate": 0.5, "strong": 0.85}


def parse_timecode(tc: str) -> float:
    """'M:SS(.ms)' or plain seconds -> seconds."""
    tc = tc.strip()
    if ":" in tc:
        mm, ss = tc.split(":", 1)
        return int(mm) * 60 + float(ss)
    return float(tc)


def parse_windows(spec: str) -> list[tuple[float, float]]:
    """'0:25-0:45,1:03-1:11' -> [(25.0,45.0),(63.0,71.0)]."""
    windows = []
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        a, b = chunk.split("-", 1)
        windows.append((parse_timecode(a), parse_timecode(b)))
    return windows


def _env_expr(windows: list[tuple[float, float]], ramp: float) -> str:
    """A trapezoidal 0..1 envelope: rises over `ramp`, holds at 1 inside each
    window, falls over `ramp`. Windows are assumed non-overlapping."""
    # ffmpeg's expression evaluator only allows 2-arg min/max, so nest them.
    terms = []
    for s, e in windows:
        rise = f"(t-{s:g})/{ramp:g}"
        fall = f"({e:g}-t)/{ramp:g}"
        terms.append(f"min(min({rise},{fall}),1)")
    inner = terms[0]
    for term in terms[1:]:
        inner = f"max({inner},{term})"
    return f"min(1,max(0,{inner}))"


def _orbit_subgraph(src: str, out: str, tag: str, hz: float, depth: float,
                    reverb: float) -> list[str]:
    """Chains that turn a stereo input [src] into a real L/R orbit [out]:
    mono-sum -> split -> complementary sine gains -> merge back to stereo.
    This produces guaranteed, controllable motion (unlike apulsator on a
    near-mono source)."""
    # Unity at centre (gain 1, not 0.5) so the upper band keeps its level when the
    # orbit fades in — otherwise the highs dip ~6 dB inside each window. Peaks can
    # reach 1+depth; the float intermediate + final limiter absorb that.
    lgain = f"1+{depth:g}*sin(2*PI*{hz:g}*t)"
    rgain = f"1-{depth:g}*sin(2*PI*{hz:g}*t)"
    chains = [
        f"[{src}]pan=mono|c0=0.5*c0+0.5*c1[{tag}m]",
        f"[{tag}m]asplit=2[{tag}l][{tag}r]",
        f"[{tag}l]volume=volume='{lgain}':eval=frame[{tag}lg]",
        f"[{tag}r]volume=volume='{rgain}':eval=frame[{tag}rg]",
        f"[{tag}lg][{tag}rg]amerge=inputs=2,aformat=channel_layouts=stereo[{tag}st]",
    ]
    if reverb > 0:
        chains.append(f"[{tag}st]aecho=0.8:0.85:60:{reverb:g}[{out}]")
    else:
        chains.append(f"[{tag}st]anull[{out}]")
    return chains


def _build_filtergraph(
    xover: float,
    hz: float,
    depth: float,
    reverb: float,
    windows: list[tuple[float, float]] | None,
    ramp: float,
) -> str:
    # Low band: summed to mono and kept dead-centre.
    low = f"lowpass=f={xover:g},pan=stereo|c0=0.5*c0+0.5*c1|c1=0.5*c0+0.5*c1"

    if not windows:
        chains = [
            "[0:a]asplit=2[low][high]",
            f"[low]{low}[lowc]",
            f"[high]highpass=f={xover:g}[hi]",
            *_orbit_subgraph("hi", "horb", "o", hz, depth, reverb),
            "[lowc][horb]amix=inputs=2:normalize=0[out]",
        ]
        return ";".join(chains)

    wet = _env_expr(windows, ramp)
    dry = f"1-({wet})"
    chains = [
        "[0:a]asplit=2[low][high]",
        f"[low]{low}[lowc]",
        f"[high]highpass=f={xover:g}[hi]",
        "[hi]asplit=2[hidry][hiwet]",
        f"[hidry]volume=volume='{dry}':eval=frame[hdry]",
        *_orbit_subgraph("hiwet", "horb", "o", hz, depth, reverb),
        f"[horb]volume=volume='{wet}':eval=frame[hwet]",
        "[hdry][hwet]amix=inputs=2:normalize=0[hmix]",
        "[lowc][hmix]amix=inputs=2:normalize=0[out]",
    ]
    return ";".join(chains)


def render(
    src: str,
    out: str,
    *,
    windows: list[tuple[float, float]] | None = None,
    xover: float = 200.0,
    period_s: float = 10.0,
    intensity: str = "mild",
    reverb: float = 0.2,
    ramp: float = 0.08,
    normalize_to: LoudnessTarget | None = None,
    bit_depth: int = 24,
) -> str:
    """Render the spatial version, then (by default) re-normalize so loudness and
    true peak stay on target."""
    depth = INTENSITY.get(intensity, INTENSITY["mild"])
    hz = 1.0 / period_s
    graph = _build_filtergraph(xover, hz, depth, reverb, windows, ramp)

    target = normalize_to if normalize_to is not None else TARGETS["streaming"]

    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    try:
        args = [
            ff.ffmpeg(), "-hide_banner", "-y", "-i", src,
            "-filter_complex", graph, "-map", "[out]",
            "-c:a", "pcm_f32le", tmp.name,  # float: no clipping before the limiter
        ]
        ff.run(args)
        # Keep the spatial master on the same loudness/peak target as the others.
        mastering.render(tmp.name, out, "", target, bit_depth=bit_depth, verify=False)
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
    return out
