"""Storage for *learned* profiles.

A profile is what the agent extracts from a before/after pair (see learn.py):
the EQ move, a stereo-width change, and a dynamics hint. Profiles are saved as
plain JSON so you can read and tweak them by hand, and re-applied to new tracks.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field


def profiles_dir() -> str:
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    d = os.path.join(here, "profiles")
    os.makedirs(d, exist_ok=True)
    return d


@dataclass
class LearnedProfile:
    name: str
    # EQ match curve as (frequency_hz, gain_db) points, increasing in frequency.
    eq_curve: list[tuple[float, float]] = field(default_factory=list)
    width_factor: float = 1.0       # >1 = wider, <1 = narrower
    dynamics_ratio: float = 1.0     # 1 = no comp; >1 = compress toward 'after'
    level_db: float = 0.0           # overall level delta observed
    meta: dict = field(default_factory=dict)

    def to_ffmpeg(self) -> str:
        """Build the ffmpeg chain that reproduces this learned transform."""
        parts: list[str] = []

        if self.eq_curve:
            entries = ";".join(f"entry({f:.0f},{g:.2f})" for f, g in self.eq_curve)
            parts.append(f"firequalizer=gain_entry='{entries}'")

        if self.dynamics_ratio > 1.05:
            ratio = min(self.dynamics_ratio, 6.0)
            parts.append(
                f"acompressor=threshold=-20dB:ratio={ratio:.1f}:"
                "attack=8:release=120:makeup=0"
            )

        if abs(self.width_factor - 1.0) > 0.05:
            slev = max(0.0, min(2.0, self.width_factor))
            parts.append(f"stereotools=mlev=1:slev={slev:.2f}")

        return ",".join(parts)


def save(profile: LearnedProfile, directory: str | None = None) -> str:
    directory = directory or profiles_dir()
    path = os.path.join(directory, f"{profile.name}.json")
    data = asdict(profile)
    data["eq_curve"] = [[float(f), float(g)] for f, g in profile.eq_curve]
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    return path


def load(name: str, directory: str | None = None) -> LearnedProfile:
    directory = directory or profiles_dir()
    path = name if os.path.isfile(name) else os.path.join(directory, f"{name}.json")
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    data["eq_curve"] = [(float(f), float(g)) for f, g in data.get("eq_curve", [])]
    return LearnedProfile(**data)


def list_profiles(directory: str | None = None) -> list[str]:
    directory = directory or profiles_dir()
    return sorted(
        os.path.splitext(f)[0]
        for f in os.listdir(directory)
        if f.endswith(".json")
    )
