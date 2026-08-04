"""Vocal production on a finished stereo mix.

Pipeline: AI-separate the vocal from the beat -> mild auto-tune -> studio FX
(de-ess, presence, doubling/width, delay throw, lush reverb) -> drop it back on
the beat. Aimed at a modern hip-hop / trap lead-vocal sound.

Heavy deps (torch/demucs, librosa, psola, pedalboard) are imported lazily so the
rest of the package works without them.
"""
from __future__ import annotations

import glob
import os
import subprocess
import sys
import tempfile

import numpy as np

from . import ffmpeg_utils as ff

# ---- 1. separation --------------------------------------------------------

def separate(src: str, out_dir: str, model: str = "htdemucs") -> tuple[str, str]:
    """Split `src` into (vocals, accompaniment) with Demucs. Returns both paths."""
    os.makedirs(out_dir, exist_ok=True)
    cmd = [
        sys.executable, "-m", "demucs", "--two-stems", "vocals",
        "-n", model, "-o", out_dir, src,
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(f"Demucs separation failed:\n{proc.stdout[-1500:]}")

    vocals = glob.glob(os.path.join(out_dir, model, "*", "vocals.wav"))
    accomp = glob.glob(os.path.join(out_dir, model, "*", "no_vocals.wav"))
    if not vocals or not accomp:
        raise RuntimeError("Demucs ran but stems were not found.")
    return vocals[0], accomp[0]


# ---- 2. auto-tune ---------------------------------------------------------

# Major/minor scale semitone sets (0=root) for optional key snapping.
_SCALES = {
    "chromatic": list(range(12)),
    "major": [0, 2, 4, 5, 7, 9, 11],
    "minor": [0, 2, 3, 5, 7, 8, 10],
}


def _snap_midi(midi: np.ndarray, root: int, scale: list[int]) -> np.ndarray:
    """Snap each MIDI value to the nearest allowed scale degree."""
    out = np.copy(midi)
    for i, m in enumerate(midi):
        if np.isnan(m):
            continue
        candidates = [root + s + 12 * o for o in range(-1, 11) for s in scale]
        out[i] = min(candidates, key=lambda c: abs(c - m))
    return out


def autotune(src: str, out: str, *, strength: float = 0.7,
             key: str | None = None, scale: str = "chromatic") -> str:
    """Pitch-correct a (mono) vocal. strength 0..1 (1 = hard snap, ~0.6 = mild)."""
    import librosa
    import psola
    import soundfile as sf

    y, sr = librosa.load(src, sr=None, mono=True)
    fmin = librosa.note_to_hz("C2")
    fmax = librosa.note_to_hz("C6")
    frame_length = 2048
    hop_length = frame_length // 4

    f0, _, _ = librosa.pyin(
        y, fmin=fmin, fmax=fmax, sr=sr,
        frame_length=frame_length, hop_length=hop_length,
    )

    midi = librosa.hz_to_midi(f0)
    root = librosa.note_to_midi(key + "0") % 12 if key else 0
    snapped = _snap_midi(midi, root, _SCALES.get(scale, _SCALES["chromatic"]))

    # Mild correction: move partway from sung pitch to the snapped note.
    corrected = midi + strength * (snapped - midi)
    corrected_hz = librosa.midi_to_hz(corrected)
    corrected_hz[np.isnan(f0)] = np.nan  # leave unvoiced frames alone

    tuned = psola.vocode(y, sample_rate=int(sr), target_pitch=corrected_hz,
                         fmin=fmin, fmax=fmax)
    sf.write(out, tuned.astype(np.float32), int(sr))
    return out


# ---- 3. FX chain (pedalboard) ---------------------------------------------

# style -> knobs. "travis" = melodic, spacey, doubled. "clean" = subtle.
FX_STYLES = {
    "travis": dict(reverb=0.28, delay=0.20, chorus=0.35, presence=3.0, air=2.5),
    "clean": dict(reverb=0.12, delay=0.08, chorus=0.15, presence=2.0, air=1.5),
    "wide": dict(reverb=0.22, delay=0.15, chorus=0.5, presence=2.5, air=2.0),
}


def apply_fx(src: str, out: str, *, style: str = "travis",
             delay_time: float = 0.23) -> str:
    """De-ess, then a pedalboard vocal chain: gate → compress → presence/air →
    doubling (chorus) → delay throw → lush reverb → limiter."""
    from pedalboard import (Chorus, Compressor, Delay, Gain, HighpassFilter,
                            HighShelfFilter, Limiter, NoiseGate, PeakFilter,
                            Pedalboard, Reverb)
    from pedalboard.io import AudioFile

    k = FX_STYLES.get(style, FX_STYLES["travis"])

    # De-ess first (ffmpeg), since pedalboard has no de-esser.
    deessed = tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name
    ff.run([ff.ffmpeg(), "-hide_banner", "-y", "-i", src,
            "-af", "deesser=i=0.2", deessed])

    board = Pedalboard([
        HighpassFilter(cutoff_frequency_hz=90),
        NoiseGate(threshold_db=-45, ratio=2, release_ms=200),  # tame bleed/hiss
        Compressor(threshold_db=-18, ratio=3, attack_ms=5, release_ms=120),
        PeakFilter(cutoff_frequency_hz=2800, gain_db=k["presence"], q=1.0),
        HighShelfFilter(cutoff_frequency_hz=8000, gain_db=k["air"]),
        Chorus(rate_hz=0.7, depth=0.25, mix=k["chorus"]),
        Delay(delay_seconds=delay_time, feedback=0.25, mix=k["delay"]),
        Reverb(room_size=0.55, damping=0.4, wet_level=k["reverb"],
               dry_level=0.9, width=1.0),
        Gain(gain_db=0.0),
        Limiter(threshold_db=-1.0),
    ])

    try:
        with AudioFile(deessed) as f:
            audio = f.read(f.frames)
            sr = f.samplerate
        # Make it stereo so chorus/reverb can widen it.
        if audio.shape[0] == 1:
            audio = np.repeat(audio, 2, axis=0)
        effected = board(audio, sr)
        with AudioFile(out, "w", sr, effected.shape[0]) as f:
            f.write(effected)
    finally:
        try:
            os.unlink(deessed)
        except OSError:
            pass
    return out


# ---- 4. recombine ---------------------------------------------------------

def recombine(vocal: str, accompaniment: str, out: str, *,
              vocal_gain_db: float = 0.0) -> str:
    """Sum the processed vocal back with the beat (no level normalization —
    the master stage handles final loudness)."""
    args = [
        ff.ffmpeg(), "-hide_banner", "-y",
        "-i", vocal, "-i", accompaniment,
        "-filter_complex",
        f"[0:a]volume={vocal_gain_db:.1f}dB[v];"
        f"[v][1:a]amix=inputs=2:normalize=0[out]",
        "-map", "[out]", "-c:a", "pcm_f32le", out,
    ]
    ff.run(args)
    return out


# ---- 5. orchestrate -------------------------------------------------------

def produce(src: str, out_wav: str, *, style: str = "travis",
            tune_strength: float = 0.7, key: str | None = None,
            scale: str = "chromatic", vocal_gain_db: float = 1.0,
            log=lambda m: None) -> str:
    """Full vocal chain: separate -> auto-tune -> FX -> recombine.
    Returns the combined (pre-master) stereo wav path."""
    work = tempfile.mkdtemp(prefix="vocalfx_")
    log("Separating vocal from the beat (this is the slow part)…")
    vocals, accomp = separate(src, work)

    log("Auto-tuning the vocal…")
    tuned = os.path.join(work, "tuned.wav")
    autotune(vocals, tuned, strength=tune_strength, key=key, scale=scale)

    log("Applying vocal FX (reverb / delay / doubling)…")
    fxv = os.path.join(work, "vocal_fx.wav")
    apply_fx(tuned, fxv, style=style)

    log("Dropping the vocal back on the beat…")
    recombine(fxv, accomp, out_wav, vocal_gain_db=vocal_gain_db)
    return out_wav
