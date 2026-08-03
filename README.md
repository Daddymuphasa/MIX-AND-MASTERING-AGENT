# Mix & Mastering Agent

An **agent that mixes & masters your audio** — driven by ffmpeg, with a Python
"brain" that measures each track and decides its own moves instead of applying a
fixed preset. It encodes the exact chain that was dialed in by ear on *Chaos
thunder*, and can also **learn your own mixing pattern from a before/after pair**
and reapply it.

```
analyze  ->  decide (agent)  ->  EQ + loudness/peak target  ->  export
```

## What it does

- **Analyze** a track: integrated loudness (LUFS), true peak (dBTP), loudness
  range, crest factor, per-band tonal balance, and lossy-codec ceiling detection.
- **Master** to real targets: `streaming` (−14 LUFS / −1 dBTP), `apple` (−16),
  `loud` (−9), `club`, `cd`. Transparent 2-pass loudnorm for streaming; a
  true-peak-safe limiter for the loud targets.
- **Adapt the EQ per track**: starts from the proven curve and nudges it — easing
  the bass on a boomy track, opening the top on a dull one — always gently, with
  a written reason for every move.
- **8D / spatial orbit**: keeps the low end mono and centred while the upper band
  orbits the head, across the whole track or gated to specific time windows.
- **Learn a pattern**: give it a dry and a mixed version of the same performance;
  it recovers the EQ curve, stereo-width and dynamics change, and saves a reusable
  profile.
- **Export** distribution-ready files: MP3 320 kbps CBR and 24-bit FLAC, both at
  44.1 kHz (soxr resampling).

## Requirements

- **ffmpeg** on your PATH (the DSP engine). Check with `ffmpeg -version`.
- **Python 3.10+**. A virtual environment is already set up in `.venv`.

If you ever need to recreate the environment:

```bash
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements.txt
```

## Usage

Run everything through the `.venv` Python. On Windows (Git Bash / PowerShell):

```bash
./.venv/Scripts/python.exe -m mixmaster <command> ...
```

### Analyze — read the track and see the plan

```bash
./.venv/Scripts/python.exe -m mixmaster analyze "song.mp3"
```

### Master — deliver streaming + loud, plus MP3/FLAC

```bash
./.venv/Scripts/python.exe -m mixmaster master "song.wav" --targets streaming,loud
```

Useful flags:

- `--targets streaming,loud,apple,club,cd` — one or more.
- `--tone brighter darker more-bass less-bass warmer cleaner` — nudge the sound.
- `--no-adapt` — apply the proven EQ verbatim, skip per-track adaptation.
- `--formats wav,mp3320,flac24,flac16` — which deliverables to write.
- `--out "DIR"` — output folder (default: a folder next to the input).

### 8D / spatial

```bash
# whole track
./.venv/Scripts/python.exe -m mixmaster spatial "master.wav" --intensity mild
# only inside sections (mm:ss ranges)
./.venv/Scripts/python.exe -m mixmaster spatial "master.wav" \
    --windows "0:25-0:45,1:03-1:11,2:06-2:23,3:01-3:16" --intensity mild
```

`--intensity mild|moderate|strong`, `--period 10` (seconds per rotation).
Best on headphones; it collapses back to near-normal on a mono speaker.

### Learn your mixing pattern (great for vocals)

Send a **dry / unmixed** file and a **mixed** version of the *same* performance:

```bash
./.venv/Scripts/python.exe -m mixmaster learn \
    --before "vocal_dry.wav" --after "vocal_mixed.wav" --name my-vocal
```

Then apply it to a new take:

```bash
./.venv/Scripts/python.exe -m mixmaster apply "new_vocal.wav" --profile my-vocal
./.venv/Scripts/python.exe -m mixmaster profiles      # list learned profiles
```

The cleaner the pairing (same take, one processed), the sharper the match.
Learned profiles are saved as readable JSON in `profiles/` — you can hand-tweak them.

## The proven default ("chaos-thunder-v3")

The default tonal profile is the curve that was approved by ear:

| Move | Setting |
|------|---------|
| Subsonic filter | high-pass @ 30 Hz |
| Bass | low shelf **+1.5 dB @ 100 Hz** |
| Mud | **−2 dB @ 300 Hz** (Q≈1) |
| Presence | **+2 dB @ 3.5 kHz** |
| Air | high shelf **+2.5 dB @ 9 kHz** |

The agent scales these to the track it actually measures.

## Fidelity notes

- **Master from the original WAV** exported by your DAW when you can. A 192 kbps
  MP3 has a hard ~18.5 kHz ceiling and is already brickwalled — the agent flags
  this and works within it, but the WAV sounds clearly better.
- A stereo bounce can only be **mastered**. To truly **mix** (rebalance drums /
  bass / vocals) the agent needs **stems** or per-track files.

## Project layout

```
mixmaster/
  analysis.py   measure loudness / peak / dynamics / tonal balance
  agent.py      the brain: decide EQ + loudness from the analysis
  presets.py    loudness targets + tonal profiles
  mastering.py  render to target (loudnorm / limiter)
  spatial.py    8D orbit (whole-track or gated windows)
  learn.py      before/after -> learned profile (FFT match)
  profiles.py   save/load/apply learned profiles
  export.py     44.1 kHz MP3 320 / 24-bit FLAC / WAV
  report.py     human-readable read-outs
  cli.py        command-line front end
profiles/       your learned profiles (JSON)
```
