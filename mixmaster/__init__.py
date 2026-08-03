"""mixmaster — an ffmpeg-driven agent that mixes & masters audio tracks.

The pipeline mirrors how a mastering engineer works:
    analyze  ->  decide (agent)  ->  EQ + loudness/peak target  ->  export

It can also *learn* your mixing pattern from a before/after pair and reapply it.
"""

__version__ = "0.1.0"
