"""Targets and tonal starting points.

LoudnessTarget = where the master should land (LUFS + true-peak ceiling).
TonalProfile   = the EQ starting point (the proven 'Chaos thunder v3' curve is
                 the default; the agent adapts it per track in agent.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class LoudnessTarget:
    name: str
    lufs: float
    true_peak: float
    mode: str  # "linear" (transparent loudnorm) or "loud" (gain + limiter)
    note: str = ""


# Where different destinations want your master to sit.
TARGETS: dict[str, LoudnessTarget] = {
    "streaming": LoudnessTarget(
        "streaming", -14.0, -1.0, "linear",
        "Spotify / Apple / YouTube reference. Transparent, no platform turn-down.",
    ),
    "apple": LoudnessTarget(
        "apple", -16.0, -1.0, "linear", "Apple Music 'Sound Check' reference.",
    ),
    "loud": LoudnessTarget(
        "loud", -9.0, -1.0, "loud",
        "SoundCloud / club level. Competitively loud, true-peak safe.",
    ),
    "club": LoudnessTarget(
        "club", -7.5, -1.0, "loud", "Very hot. Only for DJ/club playback.",
    ),
    "cd": LoudnessTarget(
        "cd", -11.0, -0.3, "loud", "CD / physical master.",
    ),
}


@dataclass
class EQBand:
    """One filter in the tonal chain, expressed so it maps straight to ffmpeg."""
    kind: str  # "highpass" | "lowshelf" | "peak" | "highshelf"
    freq: float
    gain_db: float = 0.0
    q: float = 1.0

    def to_ffmpeg(self) -> str | None:
        if self.kind == "highpass":
            return f"highpass=f={self.freq:g}"
        if abs(self.gain_db) < 0.05:
            return None  # a 0 dB move is a no-op; skip it
        if self.kind == "lowshelf":
            return f"bass=g={self.gain_db:g}:f={self.freq:g}"
        if self.kind == "highshelf":
            return f"treble=g={self.gain_db:g}:f={self.freq:g}"
        if self.kind == "peak":
            return (
                f"equalizer=f={self.freq:g}:width_type=q:"
                f"width={self.q:g}:g={self.gain_db:g}"
            )
        return None


@dataclass
class TonalProfile:
    name: str
    bands: list[EQBand] = field(default_factory=list)

    def to_ffmpeg(self) -> str:
        parts = [b.to_ffmpeg() for b in self.bands]
        return ",".join(p for p in parts if p)


def chaos_thunder_v3() -> TonalProfile:
    """The exact corrective curve dialed in on 'Chaos thunder' (v3): clean the
    subsonic + low-mid mud, gently open presence and air. Used as the default
    starting point; agent.py scales these to the track it actually sees."""
    return TonalProfile(
        "chaos-thunder-v3",
        [
            EQBand("highpass", 30),
            EQBand("lowshelf", 100, gain_db=1.5),
            EQBand("peak", 300, gain_db=-2.0, q=1.0),
            EQBand("peak", 3500, gain_db=2.0, q=1.2),
            EQBand("highshelf", 9000, gain_db=2.5),
        ],
    )


def flat() -> TonalProfile:
    """Just a protective subsonic filter — let the agent decide everything else."""
    return TonalProfile("flat", [EQBand("highpass", 30)])


def vocal_clarity_filter() -> str:
    """Make a lead vocal read clearer and more 'produced' on a finished stereo
    mix: pull a little box out of the low-mids, add upper-mid intelligibility,
    and tame sibilance with a gentle de-esser.

    Note: this improves *clarity*, not *pitch*. Auto-tune needs the isolated
    vocal stem — you can't pitch-correct one voice inside a stereo bounce."""
    return (
        "equalizer=f=450:width_type=q:width=1.2:g=-1.5,"
        "equalizer=f=2800:width_type=q:width=1.5:g=1.8,"
        "deesser=i=0.15"
    )


PROFILES = {
    "chaos-thunder-v3": chaos_thunder_v3,
    "flat": flat,
}
