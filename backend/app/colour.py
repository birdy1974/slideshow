"""The colour description every file this app writes shares.

Kept in its own module because two very different producers have to agree on it:
the render pipeline (whose stitched MP4 would otherwise carry one part's
description and show every other part wrongly — docs/timeline-colour-normalisation.md)
and the browser-facing proxies in `video_preview`, which are played on their own and
therefore have to look like the render, not like the source.

`TIMELINE_COLOUR_ARGS` stamps what `SCALE_COLOUR` proved the pixels are. Pass one
without the other and the file lies: stamp-only leaves full-range data declared
limited, convert-only leaves the container describing whatever the first input
happened to be.
"""
from __future__ import annotations

TIMELINE_COLOURSPACE = "bt709"

# Options for an H.264 output stream; encoder-agnostic, so the same list is right
# for libx264, Quick Sync and VA-API.
TIMELINE_COLOUR_ARGS = ["-color_range", "tv", "-colorspace", TIMELINE_COLOURSPACE,
                        "-color_primaries", TIMELINE_COLOURSPACE, "-color_trc", TIMELINE_COLOURSPACE]

# The swscale half: bring the pixels into that description. A frame already in it
# passes through unchanged.
SCALE_COLOUR = f"out_range=limited:out_color_matrix={TIMELINE_COLOURSPACE}"


def colour_scale(positions: str = "") -> str:
    """A `scale=…` filter carrying the colour rule, with any size options first.

    `positions` is the positional part FFmpeg accepts only at the head
    (`640:-2`, `1920:1080:force_original_aspect_ratio=…`), so callers that resize
    and callers that only re-tag go through the same function — the rule cannot be
    skipped by a producer that is not resizing at all.
    """
    return f"scale={positions}:{SCALE_COLOUR}" if positions else f"scale={SCALE_COLOUR}"
