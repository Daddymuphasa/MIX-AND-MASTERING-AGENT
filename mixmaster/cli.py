"""Command-line front end for the mix & mastering agent.

    python -m mixmaster analyze  track.mp3
    python -m mixmaster master   track.mp3 --targets streaming,loud
    python -m mixmaster spatial  track.wav --windows "0:25-0:45,1:03-1:11"
    python -m mixmaster learn    --before dry_vocal.wav --after mixed_vocal.wav --name my-vocal
    python -m mixmaster apply    new_vocal.wav --profile my-vocal
    python -m mixmaster profiles
"""
from __future__ import annotations

import argparse
import os
import sys

from . import analysis as _analysis
from . import export as _export
from . import learn as _learn
from . import mastering as _mastering
from . import profiles as _profiles
from . import report as _report
from . import spatial as _spatial
from .agent import plan as make_plan
from .presets import PROFILES, TARGETS


def _default_outdir(inp: str, suffix: str = "MixMaster") -> str:
    d = os.path.dirname(os.path.abspath(inp))
    stem = os.path.splitext(os.path.basename(inp))[0]
    return os.path.join(d, f"{stem} - {suffix}")


def _stem(inp: str) -> str:
    return os.path.splitext(os.path.basename(inp))[0]


# ---- commands --------------------------------------------------------------

def cmd_analyze(args: argparse.Namespace) -> int:
    a = _analysis.analyze(args.input)
    print(_report.analysis_report(a))
    target = TARGETS[args.target]
    p = make_plan(a, target, tone=args.tone, adapt=not args.no_adapt)
    print()
    print(_report.plan_report(p))
    return 0


def cmd_master(args: argparse.Namespace) -> int:
    inp = args.input
    outdir = args.out or _default_outdir(inp)
    os.makedirs(outdir, exist_ok=True)
    stem = _stem(inp)

    print(f"Analyzing {os.path.basename(inp)} ...")
    a = _analysis.analyze(inp)
    print(_report.analysis_report(a))

    base = PROFILES[args.profile]() if args.profile in PROFILES else None
    targets = [TARGETS[t.strip()] for t in args.targets.split(",")]
    formats = [f.strip() for f in args.formats.split(",") if f.strip()]

    # Tonal plan is shared across targets; only loudness differs.
    shown_plan = make_plan(a, targets[0], base=base, tone=args.tone,
                           adapt=not args.no_adapt)
    print()
    print(_report.plan_report(shown_plan))
    if args.vocal:
        print("  + vocal clarity (de-ess + presence)")
    print()

    from .presets import vocal_clarity_filter
    vocal = vocal_clarity_filter() if args.vocal else ""

    results = []
    for target in targets:
        p = make_plan(a, target, base=base, tone=args.tone, adapt=not args.no_adapt)
        eq = f"{p.eq_filter},{vocal}" if (p.eq_filter and vocal) else (p.eq_filter or vocal)
        master_wav = os.path.join(outdir, f"{stem} - MASTER ({target.name}).wav")
        print(f"Rendering {target.name} master -> {os.path.basename(master_wav)}")
        res = _mastering.render(
            inp, master_wav, eq, target,
            sample_rate=None, bit_depth=24, verify=True,
        )
        results.append(res)

        # Delivery formats (44.1kHz) for this master.
        delivery = [f for f in formats if f not in {"wav", "wav24"}]
        if delivery:
            _export.export(master_wav, outdir, f"{stem} - {target.name}", delivery)

    print()
    print(_report.result_report(results))
    print(f"\nAll files in: {outdir}")
    return 0


def cmd_spatial(args: argparse.Namespace) -> int:
    inp = args.input
    outdir = args.out or _default_outdir(inp)
    os.makedirs(outdir, exist_ok=True)
    stem = _stem(inp)

    windows = _spatial.parse_windows(args.windows) if args.windows else None
    target = TARGETS[args.target]
    out_wav = os.path.join(outdir, f"{stem} - 8D.wav")

    where = (
        f"{len(windows)} section(s)" if windows else "whole track"
    )
    print(f"Rendering 8D orbit ({args.intensity}, {where}) -> {os.path.basename(out_wav)}")
    _spatial.render(
        inp, out_wav, windows=windows, intensity=args.intensity,
        period_s=args.period, normalize_to=target,
    )
    formats = [f.strip() for f in args.formats.split(",")
               if f.strip() and f.strip() not in {"wav", "wav24"}]
    if formats:
        _export.export(out_wav, outdir, f"{stem} - 8D", formats)

    res = _mastering._measure_loudness(out_wav)
    print(f"  -> {res.get('input_i', float('nan')):.1f} LUFS / "
          f"{res.get('input_tp', float('nan')):+.2f} dBTP")
    print(f"\nSaved to: {outdir}")
    return 0


def cmd_learn(args: argparse.Namespace) -> int:
    print(f"Learning '{args.name}' from:\n  before: {args.before}\n  after:  {args.after}")
    prof = _learn.learn(args.before, args.after, args.name)
    path = _profiles.save(prof)
    print(f"\nLearned profile saved -> {path}\n")
    print("  EQ match curve (Hz: dB):")
    for f, g in prof.eq_curve:
        print(f"     {f:>7.0f} : {g:+5.2f}")
    print(f"  Stereo width factor : {prof.width_factor:.2f} "
          f"({'wider' if prof.width_factor>1 else 'narrower'})")
    print(f"  Dynamics ratio      : {prof.dynamics_ratio:.2f}")
    print(f"  Level delta         : {prof.level_db:+.2f} dB")
    print(f"\n  ffmpeg chain: {prof.to_ffmpeg() or '(flat)'}")
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    inp = args.input
    outdir = args.out or _default_outdir(inp)
    os.makedirs(outdir, exist_ok=True)
    stem = _stem(inp)

    prof = _profiles.load(args.profile)
    chain = prof.to_ffmpeg()
    target = TARGETS[args.target]
    out_wav = os.path.join(outdir, f"{stem} - {prof.name}.wav")
    print(f"Applying learned profile '{prof.name}' -> {os.path.basename(out_wav)}")
    res = _mastering.render(inp, out_wav, chain, target, bit_depth=24, verify=True)

    formats = [f.strip() for f in args.formats.split(",")
               if f.strip() and f.strip() not in {"wav", "wav24"}]
    if formats:
        _export.export(out_wav, outdir, f"{stem} - {prof.name}", formats)

    print(f"  -> {res.measured_lufs:.1f} LUFS / {res.measured_tp:+.2f} dBTP")
    print(f"\nSaved to: {outdir}")
    return 0


def cmd_profiles(args: argparse.Namespace) -> int:
    names = _profiles.list_profiles()
    if not names:
        print("No learned profiles yet. Create one with:  "
              "python -m mixmaster learn --before dry.wav --after mixed.wav --name my-vocal")
        return 0
    print("Learned profiles:")
    for n in names:
        p = _profiles.load(n)
        print(f"  • {n}  (from {p.meta.get('before','?')} -> {p.meta.get('after','?')})")
    return 0


# ---- parser ----------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mixmaster",
        description="An agent that mixes & masters audio the way it was proven on your tracks.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    def add_common_out(sp):
        sp.add_argument("--out", help="Output directory (default: next to input).")
        sp.add_argument("--formats", default="wav,mp3320,flac24",
                        help="Comma list: wav,mp3320,flac24,flac16 (default all).")

    sp = sub.add_parser("analyze", help="Measure a track and show the recommended plan.")
    sp.add_argument("input")
    sp.add_argument("--target", default="streaming", choices=list(TARGETS))
    sp.add_argument("--tone", nargs="*", default=[])
    sp.add_argument("--no-adapt", action="store_true")
    sp.set_defaults(func=cmd_analyze)

    sp = sub.add_parser("master", help="Master to one or more loudness targets.")
    sp.add_argument("input")
    sp.add_argument("--targets", default="streaming,loud",
                    help="Comma list from: " + ", ".join(TARGETS))
    sp.add_argument("--profile", default="chaos-thunder-v3",
                    help="Tonal starting point: " + ", ".join(PROFILES))
    sp.add_argument("--tone", nargs="*", default=[],
                    help="Nudges: brighter darker more-bass less-bass warmer cleaner")
    sp.add_argument("--no-adapt", action="store_true",
                    help="Use the profile EQ verbatim (skip per-track adaptation).")
    sp.add_argument("--vocal", action="store_true",
                    help="Add vocal clarity (de-ess + presence). Clarity, not pitch.")
    add_common_out(sp)
    sp.set_defaults(func=cmd_master)

    sp = sub.add_parser("spatial", help="Render an 8D / spatial orbit version.")
    sp.add_argument("input")
    sp.add_argument("--windows", help='e.g. "0:25-0:45,1:03-1:11" (default: whole track)')
    sp.add_argument("--intensity", default="mild", choices=list(_spatial.INTENSITY))
    sp.add_argument("--period", type=float, default=10.0, help="Rotation period (s).")
    sp.add_argument("--target", default="streaming", choices=list(TARGETS))
    add_common_out(sp)
    sp.set_defaults(func=cmd_spatial)

    sp = sub.add_parser("learn", help="Learn a mix pattern from a before/after pair.")
    sp.add_argument("--before", required=True, help="Unmixed / dry file.")
    sp.add_argument("--after", required=True, help="Mixed file (same performance).")
    sp.add_argument("--name", required=True, help="Profile name to save.")
    sp.set_defaults(func=cmd_learn)

    sp = sub.add_parser("apply", help="Apply a learned profile to a new track.")
    sp.add_argument("input")
    sp.add_argument("--profile", required=True)
    sp.add_argument("--target", default="streaming", choices=list(TARGETS))
    add_common_out(sp)
    sp.set_defaults(func=cmd_apply)

    sp = sub.add_parser("profiles", help="List learned profiles.")
    sp.set_defaults(func=cmd_profiles)

    return p


def main(argv: list[str] | None = None) -> int:
    # Windows consoles default to cp1252; our reports use box/bar glyphs.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:  # noqa: BLE001 - surface a clean message to the CLI
        print(f"\nError: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
