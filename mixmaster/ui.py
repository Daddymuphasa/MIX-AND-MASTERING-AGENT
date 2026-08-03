"""A drag-and-drop web UI for the mix & mastering agent.

Launch with:  python -m mixmaster.ui   (or double-click MixMaster.bat)
Opens in your browser. Drop a WAV/MP3 in, hit one button, get your masters.
"""
from __future__ import annotations

import os
from pathlib import Path

import gradio as gr

from . import analysis as _analysis
from . import export as _export
from . import mastering as _mastering
from . import profiles as _profiles
from . import spatial as _spatial
from .agent import plan as make_plan
from .presets import PROFILES, TARGETS, chaos_thunder_v3

AUTO = "(none — let the agent decide)"


def _out_dir(stem: str) -> str:
    d = Path.home() / "MixMaster Renders" / stem
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


def _report_md(a, eq_filter, reasons) -> str:
    codec = f"{a.info.codec} {a.info.sample_rate/1000:.1f}kHz"
    if a.info.bit_rate:
        codec += f" @ {a.info.bit_rate//1000}kbps"
    lines = [
        "### What I heard",
        "",
        "| | |",
        "|---|---|",
        f"| Format | {codec} ({a.info.duration:.0f}s) |",
        f"| Loudness | {a.integrated_lufs:.1f} LUFS |",
        f"| True peak | {a.true_peak_dbtp:+.2f} dBTP |",
        f"| Dynamics (crest) | {a.crest_db:.1f} dB |"
        if a.crest_db is not None else "",
        f"| Tonal tilt | {a.tonal_tilt:+.1f} dB "
        f"({'bright' if a.tonal_tilt > 0 else 'dark'}) |",
    ]
    if a.codec_cutoff_hz:
        lines.append(
            f"| Note | lossy source, ~{a.codec_cutoff_hz/1000:.1f}kHz ceiling — "
            "a WAV export would sound better |"
        )
    lines.append("")
    lines.append("### What I did")
    for r in reasons:
        lines.append(f"- {r}")
    lines.append("")
    lines.append(f"**EQ chain:** `{eq_filter or 'none'}`")
    return "\n".join(x for x in lines if x is not None)


def run_master(audio, targets, tones, do_8d, intensity, profile_name,
               progress=gr.Progress()):
    if not audio:
        raise gr.Error("Add a song first — drag a WAV or MP3 into the box.")

    progress(0.05, desc="Analyzing the track…")
    stem = Path(audio).stem
    outdir = _out_dir(stem)
    a = _analysis.analyze(audio)

    # Decide the tonal treatment: a learned profile, or the agent's own plan.
    if profile_name and profile_name != AUTO:
        prof = _profiles.load(profile_name)
        eq_filter = prof.to_ffmpeg()
        reasons = [f"Applied your learned **{profile_name}** sound.",
                   *_plan_loudness_reasons(a)]
    else:
        p = make_plan(a, TARGETS["streaming"], base=chaos_thunder_v3(), tone=tones)
        eq_filter, reasons = p.eq_filter, p.reasons

    want = set(targets or ["Streaming", "Loud"])
    stream_wav = loud_wav = eightd_wav = None

    if "Streaming" in want:
        progress(0.35, desc="Rendering streaming master (−14 LUFS)…")
        stream_wav = os.path.join(outdir, f"{stem} - MASTER (streaming).wav")
        _mastering.render(audio, stream_wav, eq_filter, TARGETS["streaming"],
                          verify=False)
        _export.export(stream_wav, outdir, f"{stem} - streaming", ["mp3320", "flac24"])

    if "Loud" in want:
        progress(0.6, desc="Rendering loud master (−9 LUFS)…")
        loud_wav = os.path.join(outdir, f"{stem} - MASTER (loud).wav")
        _mastering.render(audio, loud_wav, eq_filter, TARGETS["loud"], verify=False)
        _export.export(loud_wav, outdir, f"{stem} - loud", ["mp3320", "flac24"])

    if do_8d:
        progress(0.8, desc="Rendering 8D orbit…")
        base = stream_wav or loud_wav
        if base is None:  # user unchecked both masters but wants 8D
            base = os.path.join(outdir, f"{stem} - MASTER (streaming).wav")
            _mastering.render(audio, base, eq_filter, TARGETS["streaming"],
                              verify=False)
        eightd_wav = os.path.join(outdir, f"{stem} - 8D.wav")
        _spatial.render(base, eightd_wav, intensity=intensity,
                        normalize_to=TARGETS["streaming"])
        _export.export(eightd_wav, outdir, f"{stem} - 8D", ["mp3320"])

    progress(1.0, desc="Done")
    report = _report_md(a, eq_filter, reasons)
    folder = f"📁 Saved to **{outdir}** (WAV + MP3 320 + FLAC 24-bit)."
    return (
        report,
        gr.update(value=stream_wav, visible=stream_wav is not None),
        gr.update(value=loud_wav, visible=loud_wav is not None),
        gr.update(value=eightd_wav, visible=eightd_wav is not None),
        folder,
    )


def _plan_loudness_reasons(a):
    out = []
    if a.integrated_lufs == a.integrated_lufs and a.integrated_lufs > -12:
        out.append(f"Source is hot ({a.integrated_lufs:.1f} LUFS) — brought to target "
                   "so streaming platforms don't clamp it.")
    return out


def run_learn(before, after, name):
    from . import learn as _learn

    if not before or not after:
        raise gr.Error("Add both a dry (unmixed) and a mixed version.")
    if not name.strip():
        raise gr.Error("Give this sound a name (e.g. my-vocal).")
    name = name.strip().replace(" ", "-")
    prof = _learn.learn(before, after, name)
    _profiles.save(prof)

    curve = "\n".join(f"| {f:.0f} Hz | {g:+.2f} dB |" for f, g in prof.eq_curve)
    md = (
        f"### Learned **{name}** ✓\n\n"
        f"Width: {'wider' if prof.width_factor > 1 else 'narrower'} "
        f"({prof.width_factor:.2f}) · Compression: {prof.dynamics_ratio:.2f}× · "
        f"Level: {prof.level_db:+.1f} dB\n\n"
        "It's now in the **Master** tab's profile dropdown.\n\n"
        "<details><summary>EQ match curve</summary>\n\n"
        f"| Freq | Move |\n|---|---|\n{curve}\n\n</details>"
    )
    choices = [AUTO] + [f"{n}" for n in _profiles.list_profiles()]
    return md, gr.update(choices=choices)


CSS = """
#title h1 {font-size: 2.1rem; margin-bottom: 0; background: linear-gradient(90deg,#a855f7,#22d3ee);
  -webkit-background-clip: text; -webkit-text-fill-color: transparent;}
.gradio-container {max-width: 900px !important; margin: auto;}
"""


THEME = gr.themes.Soft(primary_hue="purple", secondary_hue="cyan")


def build() -> gr.Blocks:
    profile_choices = [AUTO] + _profiles.list_profiles()
    with gr.Blocks(title="MixMaster") as demo:
        gr.Markdown("# 🎛️ MixMaster\n"
                    "Drop a track in, get a finished master. "
                    "Best from a **WAV** export.", elem_id="title")

        with gr.Tab("Master a track"):
            audio = gr.Audio(label="Your song", type="filepath", sources=["upload"])
            with gr.Row():
                targets = gr.CheckboxGroup(
                    ["Streaming", "Loud"], value=["Streaming", "Loud"],
                    label="Masters to make",
                    info="Streaming = −14 LUFS (Spotify/Apple). Loud = −9 (SoundCloud/club).")
                profile = gr.Dropdown(profile_choices, value=AUTO,
                                      label="Vocal / mix sound",
                                      info="Use a sound you taught it, or let it decide.")
            with gr.Accordion("Fine-tune (optional)", open=False):
                tones = gr.CheckboxGroup(
                    ["more-bass", "less-bass", "brighter", "darker", "warmer", "cleaner"],
                    value=[], label="Nudge the tone")
                with gr.Row():
                    do_8d = gr.Checkbox(False, label="Add 8D orbit")
                    intensity = gr.Radio(["mild", "moderate", "strong"], value="mild",
                                         label="8D intensity")
            go = gr.Button("🎚️  Mix & Master", variant="primary", size="lg")

            report = gr.Markdown()
            stream_out = gr.Audio(label="Streaming master (−14 LUFS)", visible=False)
            loud_out = gr.Audio(label="Loud master (−9 LUFS)", visible=False)
            eightd_out = gr.Audio(label="8D version", visible=False)
            folder = gr.Markdown()

            go.click(run_master,
                     [audio, targets, tones, do_8d, intensity, profile],
                     [report, stream_out, loud_out, eightd_out, folder])

        with gr.Tab("Teach it your vocal sound"):
            gr.Markdown("Give it the **same performance** twice — the raw/unmixed "
                        "version and the mixed version you like. It learns the move "
                        "and can apply it to new tracks.")
            with gr.Row():
                before = gr.Audio(label="Unmixed / dry", type="filepath", sources=["upload"])
                after = gr.Audio(label="Mixed (the sound you want)", type="filepath",
                                 sources=["upload"])
            name = gr.Textbox(label="Name this sound", placeholder="my-vocal")
            learn_btn = gr.Button("🧠  Learn this sound", variant="primary")
            learn_out = gr.Markdown()
            learn_btn.click(run_learn, [before, after, name], [learn_out, profile])

    return demo


def main():
    build().launch(theme=THEME, css=CSS, inbrowser=True)


if __name__ == "__main__":
    main()
