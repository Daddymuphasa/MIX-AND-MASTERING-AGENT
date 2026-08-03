"""The brain.

Given an Analysis and a target, decide the actual moves: it starts from a tonal
profile (default: the proven Chaos-thunder curve) and *adapts* the gains to what
the track actually measures — pulling back the bass boost on an already bass-heavy
track, opening the air more on a dull one, and so on. Every decision carries a
short reason so the report can explain itself.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field

from .analysis import Analysis
from .presets import EQBand, LoudnessTarget, TonalProfile, chaos_thunder_v3

# User-facing tone nudges -> (band match, delta dB)
TONE_NUDGES: dict[str, list[tuple[str, float]]] = {
    "brighter": [("highshelf", +1.0), ("presence", +0.5)],
    "darker": [("highshelf", -1.5), ("presence", -0.5)],
    "more-bass": [("lowshelf", +1.5)],
    "less-bass": [("lowshelf", -1.5)],
    "warmer": [("lowshelf", +1.0), ("highshelf", -0.5)],
    "cleaner": [("300", -1.0), ("presence", +0.5)],  # cut mud, add clarity
}


@dataclass
class ProcessingPlan:
    tonal: TonalProfile
    target: LoudnessTarget
    reasons: list[str] = field(default_factory=list)

    @property
    def eq_filter(self) -> str:
        return self.tonal.to_ffmpeg()


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def plan(
    analysis: Analysis,
    target: LoudnessTarget,
    *,
    base: TonalProfile | None = None,
    tone: list[str] | None = None,
    adapt: bool = True,
) -> ProcessingPlan:
    tonal = deepcopy(base or chaos_thunder_v3())
    reasons: list[str] = []

    if adapt:
        _adapt(tonal, analysis, reasons)
    _apply_tone_nudges(tonal, tone or [], reasons)

    # Subsonic filter: raise it if there's a lot of rumble, keep it low otherwise.
    sub_rel = analysis.relative_bands.get("sub", 0)
    for b in tonal.bands:
        if b.kind == "highpass":
            b.freq = 35 if sub_rel > 6 else 30

    _add_loudness_reason(analysis, target, reasons)
    return ProcessingPlan(tonal=tonal, target=target, reasons=reasons)


def _find(tonal: TonalProfile, kind: str, freq: float | None = None) -> EQBand | None:
    for b in tonal.bands:
        if b.kind == kind and (freq is None or abs(b.freq - freq) < 1):
            return b
    return None


def _adapt(tonal: TonalProfile, a: Analysis, reasons: list[str]) -> None:
    """Adapt gently around the proven profile. Bands are compared *to each other*
    (a narrow band always reads low vs the full mix, so absolute band-vs-mix
    figures are meaningless). Moves are small and tightly clamped so a track like
    the reference lands right back on the sound that was dialed in by ear."""
    rel = a.relative_bands
    if not rel:
        return
    bass, low_mid, mid, air = (
        rel.get("bass", 0), rel.get("low_mid", 0),
        rel.get("mid", 0), rel.get("air", 0),
    )

    # --- Bass: only move if the lows are clearly hotter/weaker than the mids. ---
    low_shelf = _find(tonal, "lowshelf", 100)
    if low_shelf is not None:
        bass_vs_mid = bass - mid
        if bass_vs_mid > 3:  # boomy -> ease the boost
            new = _clamp(low_shelf.gain_db - (bass_vs_mid - 3) * 0.4, -3.0, 4.0)
            if new < low_shelf.gain_db - 0.2:
                reasons.append(
                    f"Lows are {bass_vs_mid:.1f} dB over the mids (boomy) — bass boost "
                    f"eased to {new:+.1f} dB @100Hz.")
            low_shelf.gain_db = new
        elif bass_vs_mid < -3:  # thin -> add weight
            new = _clamp(low_shelf.gain_db + (-3 - bass_vs_mid) * 0.4, -3.0, 4.0)
            if new > low_shelf.gain_db + 0.2:
                reasons.append(
                    f"Lows are {-bass_vs_mid:.1f} dB under the mids (thin) — bass boost "
                    f"raised to {new:+.1f} dB @100Hz.")
            low_shelf.gain_db = new

    # --- Mud: deepen the 300 Hz cut only if low-mids pile up over neighbours. ---
    mud = _find(tonal, "peak", 300)
    if mud is not None:
        mud_excess = low_mid - (bass + mid) / 2
        if mud_excess > 1:
            mud.gain_db = _clamp(mud.gain_db - (mud_excess - 1) * 0.5, -4.0, 0.0)
            reasons.append(
                f"Low-mids are {mud_excess:.1f} dB over their neighbours (mud) — "
                f"300Hz cut set to {mud.gain_db:+.1f} dB.")

    # --- Air: how far the top sits below the mids tells us how dull it reads. ---
    air_shelf = _find(tonal, "highshelf", 9000)
    if air_shelf is not None:
        air_deficit = mid - air  # bigger = duller
        if air_deficit > 14:
            add = _clamp((air_deficit - 14) * 0.12, 0.0, 1.5)
            air_shelf.gain_db = _clamp(air_shelf.gain_db + add, 0.0, 4.5)
            if add > 0.2:
                reasons.append(
                    f"Top sits {air_deficit:.0f} dB under the mids (dull) — air shelf "
                    f"opened to {air_shelf.gain_db:+.1f} dB.")
        elif air_deficit < 8:  # already bright/harsh
            air_shelf.gain_db = _clamp(air_shelf.gain_db - (8 - air_deficit) * 0.2,
                                       0.0, 4.5)
            reasons.append(
                f"Top is already forward — air shelf eased to "
                f"{air_shelf.gain_db:+.1f} dB.")

    # --- Don't try to 'add air' above a codec ceiling; move the shelf down. ---
    if a.codec_cutoff_hz and air_shelf is not None and a.codec_cutoff_hz < 19000:
        new_freq = min(air_shelf.freq, a.codec_cutoff_hz * 0.7)
        if new_freq < air_shelf.freq - 1:
            air_shelf.freq = new_freq
            reasons.append(
                f"Source is lossy (~{a.codec_cutoff_hz/1000:.1f}kHz ceiling) — air shelf "
                f"moved to {air_shelf.freq:.0f}Hz so it lifts real content, not codec hiss."
            )


def _apply_tone_nudges(
    tonal: TonalProfile, tone: list[str], reasons: list[str]
) -> None:
    for t in tone:
        nudges = TONE_NUDGES.get(t)
        if not nudges:
            continue
        for match, delta in nudges:
            band = _match_band(tonal, match)
            if band is not None:
                band.gain_db = _clamp(band.gain_db + delta, -6.0, 6.0)
        reasons.append(f"Applied '{t}' tone request.")


def _match_band(tonal: TonalProfile, match: str) -> EQBand | None:
    if match in {"lowshelf", "highshelf", "highpass"}:
        return _find(tonal, match)
    if match == "presence":
        return _find(tonal, "peak", 3500)
    if match == "300":
        return _find(tonal, "peak", 300)
    return None


def _add_loudness_reason(
    a: Analysis, target: LoudnessTarget, reasons: list[str]
) -> None:
    delta = target.lufs - a.integrated_lufs
    if a.integrated_lufs == a.integrated_lufs:  # not NaN
        if delta < -1:
            reasons.append(
                f"Source is hot at {a.integrated_lufs:.1f} LUFS — bringing it "
                f"{abs(delta):.1f} dB down to {target.lufs:.0f} LUFS ({target.name}) "
                "so platforms don't clamp it."
            )
        elif delta > 1:
            reasons.append(
                f"Source is quiet at {a.integrated_lufs:.1f} LUFS — raising it "
                f"{delta:.1f} dB toward {target.lufs:.0f} LUFS ({target.name})."
            )
    if a.true_peak_dbtp == a.true_peak_dbtp and a.true_peak_dbtp > -0.5:
        reasons.append(
            f"True peak is {a.true_peak_dbtp:+.2f} dBTP (near/over ceiling) — "
            f"limiting to {target.true_peak:.1f} dBTP to stop inter-sample clipping."
        )
