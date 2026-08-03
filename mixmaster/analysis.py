"""Measure a track the way an engineer would 'read' it before touching a fader:
integrated loudness, true peak, dynamics, and tonal balance across bands.

This is the perception half of the agent — the numbers here feed agent.py, which
decides the moves.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import ffmpeg_utils as ff

# (name, low_hz, high_hz) bands used to read tonal balance.
BANDS: list[tuple[str, float, float]] = [
    ("sub", 20, 60),
    ("bass", 60, 150),
    ("low_mid", 150, 400),
    ("mid", 400, 2000),
    ("presence", 2000, 6000),
    ("air", 8000, 16000),
    ("very_high", 16000, 20000),
]


@dataclass
class Analysis:
    path: str
    info: ff.MediaInfo
    integrated_lufs: float
    true_peak_dbtp: float
    lra: float
    threshold: float
    crest_db: float | None
    rms_db: float | None
    peak_db: float | None
    bands_db: dict[str, float] = field(default_factory=dict)
    codec_cutoff_hz: float | None = None

    # ---- derived, human-readable read-outs ----
    @property
    def relative_bands(self) -> dict[str, float]:
        """Each band's RMS relative to the full-mix RMS (dB). Positive = that band
        is more prominent than the overall level."""
        if self.rms_db is None:
            return {}
        return {name: v - self.rms_db for name, v in self.bands_db.items()}

    @property
    def tonal_tilt(self) -> float:
        """+ve = bright (air stronger than bass), -ve = dark/bass-heavy."""
        rb = self.relative_bands
        low = (rb.get("sub", 0) + rb.get("bass", 0)) / 2
        high = (rb.get("presence", 0) + rb.get("air", 0)) / 2
        return high - low


def _measure_loudness(path: str, prefilter: str | None = None) -> dict[str, float]:
    graph = "loudnorm=print_format=json"
    if prefilter:
        graph = f"{prefilter},{graph}"
    stderr = ff.measure_filter(path, graph)
    return ff.parse_loudnorm_json(stderr)


def _band_rms(path: str, lo: float, hi: float) -> float | None:
    graph = (
        f"highpass=f={lo}:poles=2,lowpass=f={hi}:poles=2,"
        "astats=measure_perchannel=none:measure_overall=RMS_level"
    )
    stats = ff.parse_astats(ff.measure_filter(path, graph))
    return stats["rms_db"]


def analyze(path: str, *, bands: bool = True) -> Analysis:
    info = ff.probe(path)

    loud = _measure_loudness(path)
    astats = ff.parse_astats(
        ff.measure_filter(path, "astats=measure_perchannel=none")
    )

    crest = astats["crest_factor"]
    crest_db = None
    if crest is not None and crest > 0:
        from math import log10

        crest_db = 20 * log10(crest)
    elif astats["peak_db"] is not None and astats["rms_db"] is not None:
        crest_db = astats["peak_db"] - astats["rms_db"]

    a = Analysis(
        path=path,
        info=info,
        integrated_lufs=loud.get("input_i", float("nan")),
        true_peak_dbtp=loud.get("input_tp", float("nan")),
        lra=loud.get("input_lra", float("nan")),
        threshold=loud.get("input_thresh", float("nan")),
        crest_db=crest_db,
        rms_db=astats["rms_db"],
        peak_db=astats["peak_db"],
    )

    if bands:
        for name, lo, hi in BANDS:
            a.bands_db[name] = _band_rms(path, lo, hi) or float("-inf")
        a.codec_cutoff_hz = _detect_codec_cutoff(a)

    return a


def _detect_codec_cutoff(a: Analysis) -> float | None:
    """Cheap heuristic: lossy sources brickwall the top octave. If the source is
    lossy and the 16-20kHz band is far below the air band, report the likely
    codec ceiling."""
    if not a.info.is_lossy:
        return None
    br = a.info.bit_rate or 0
    if br and br <= 128_000:
        return 16_000.0
    if br and br <= 192_000:
        return 18_500.0
    very_high = a.bands_db.get("very_high", float("-inf"))
    air = a.bands_db.get("air", 0)
    if very_high == float("-inf") or (air - very_high) > 35:
        return 19_000.0
    return None
