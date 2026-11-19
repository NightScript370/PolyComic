"""Reference images for image calls: downscaling / re-encoding and input-token estimates.

Why: a real book hit Google's ``GenerateContentPaidTierInputTokensPerModelPerMinute``
limit (100 000 input tokens per minute per model) on the image model - every page call
sent 3-5 full-size (~896 x 1200) PNG references.

How Gemini counts image input tokens (Google AI Studio docs, "Understand and count
tokens" and "Media resolution", checked Nov 2026):

* Classic rule: an image with both sides <= 384 px is 258 tokens; a larger image is cut
  into 768 x 768 tiles of 258 tokens each (an 896 x 1200 picture = 2 x 2 tiles = 1032).
* Gemini 3 models: a fixed budget per image set by ``media_resolution`` - low 280,
  medium 560, high 1120 (the default when unspecified), ultra_high 2240 - whatever the
  pixel size.

So references are (1) downscaled to ``IMAGE_REF_MAX_SIDE`` (default 768: one tile under
the classic rule, and still about 150 px for the face on a full-body hero sheet) and
re-encoded as JPEG, and (2) sent with an explicit per-part ``media_resolution``: the first
reference (the identity anchor - the hero reference on pages, the photo on the hero call)
at ``IMAGE_REF_MEDIA_RESOLUTION_FIRST`` (default high) and the others at
``IMAGE_REF_MEDIA_RESOLUTION`` (default medium). Estimates use the media-resolution table
when a level is set, else the tile rule; ``app.pipeline.book`` corrects them with the
``prompt_token_count`` the API reports.
"""

from __future__ import annotations

import io
import math

from PIL import Image, UnidentifiedImageError

REF_JPEG_QUALITY = 90
MEDIA_TOKENS = {"low": 280, "medium": 560, "high": 1120, "ultra_high": 2240}
MEDIA_LEVELS = tuple(MEDIA_TOKENS)
TILE = 768
TILE_TOKENS = 258
SMALL_SIDE = 384
CHARS_PER_TOKEN = 4.0


def shrunk_size(size: tuple[int, int], max_side: int) -> tuple[int, int]:
    """(w, h) after a ``thumbnail`` to ``max_side`` (unchanged when small or max_side<=0)."""
    w, h = size
    if max_side <= 0 or max(w, h) <= max_side:
        return w, h
    scale = max_side / max(w, h)
    return max(1, round(w * scale)), max(1, round(h * scale))


def shrink_ref(data: bytes, max_side: int, quality: int = REF_JPEG_QUALITY) -> bytes:
    """``data`` as an RGB JPEG with the longest side at most ``max_side`` px when it is
    larger (transparency is flattened onto white). Unchanged when it is already small
    enough, unreadable, or ``max_side`` <= 0."""
    if max_side <= 0:
        return data
    try:
        with Image.open(io.BytesIO(data)) as im:
            if max(im.size) <= max_side:
                return data
            if im.mode in ("RGBA", "LA", "P"):
                rgba = im.convert("RGBA")
                flat = Image.new("RGB", rgba.size, (255, 255, 255))
                flat.paste(rgba, mask=rgba.getchannel("A"))
                im2 = flat
            else:
                im2 = im.convert("RGB")
            im2.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
            out = io.BytesIO()
            im2.save(out, format="JPEG", quality=quality)
            return out.getvalue()
    except (UnidentifiedImageError, OSError, ValueError):
        return data


def _size(data: bytes) -> tuple[int, int] | None:
    try:
        with Image.open(io.BytesIO(data)) as im:
            return im.size
    except (UnidentifiedImageError, OSError, ValueError):
        return None


def tile_tokens(size: tuple[int, int] | None) -> int:
    """Classic Gemini image token count for an image of ``size`` (w, h)."""
    if size is None:
        return TILE_TOKENS * 4  # unknown: assume a ~1K picture
    w, h = size
    if w <= SMALL_SIDE and h <= SMALL_SIDE:
        return TILE_TOKENS
    return math.ceil(w / TILE) * math.ceil(h / TILE) * TILE_TOKENS


def image_tokens(data: bytes, max_side: int = 0, level: str = "") -> int:
    """Estimated input tokens of one reference as it will be sent."""
    if level in MEDIA_TOKENS:
        return MEDIA_TOKENS[level]
    size = _size(data)
    return tile_tokens(shrunk_size(size, max_side) if size else None)


def text_tokens(text: str) -> int:
    return math.ceil(len(text or "") / CHARS_PER_TOKEN)


def ref_levels(n: int, first: str, rest: str) -> list[str]:
    """Media-resolution level per reference ('' = not set)."""
    return [(first if i == 0 else rest) for i in range(n)]


def estimate_input_tokens(prompt: str, refs: list[bytes], *, max_side: int = 0,
                          first_level: str = "", level: str = "") -> int:
    """Estimated input tokens of one image call (prompt + references)."""
    levels = ref_levels(len(refs), first_level, level)
    return text_tokens(prompt) + sum(image_tokens(r, max_side, lv)
                                     for r, lv in zip(refs, levels, strict=True))


def normalise_level(value: str) -> str:
    """'' (unset) or one of MEDIA_LEVELS; raises ValueError otherwise."""
    v = (value or "").strip().lower()
    if v in ("", "none", "unset", "default"):
        return ""
    if v not in MEDIA_TOKENS:
        raise ValueError(f"media resolution must be one of {MEDIA_LEVELS} or empty, "
                         f"got {value!r}")
    return v
