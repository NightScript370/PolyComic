"""Validate and normalise an uploaded child photo."""

from __future__ import annotations

import io
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_EDGE = 1536
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}


class PhotoError(ValueError):
    pass


def normalise_photo(data: bytes) -> bytes:
    """Return a JPEG (EXIF-transposed, RGB, longest edge <= 1536px) or raise PhotoError."""
    if not data:
        raise PhotoError("The photo is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise PhotoError("The photo is larger than 10 MB.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as probe:
                fmt = probe.format
                probe.verify()
            if fmt not in ALLOWED_FORMATS:
                raise PhotoError("Please upload a JPEG, PNG or WebP photo.")
            im = Image.open(io.BytesIO(data))
            im.load()
    except PhotoError:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError,
            Image.DecompressionBombWarning, OSError, SyntaxError) as exc:
        raise PhotoError("That file is not a readable JPEG, PNG or WebP image.") from exc

    im = ImageOps.exif_transpose(im)
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.getchannel("A"))
        im = bg
    elif im.mode != "RGB":
        im = im.convert("RGB")
    im.thumbnail((MAX_EDGE, MAX_EDGE), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    im.save(out, format="JPEG", quality=92)
    return out.getvalue()


THUMB_WIDTH = 320


def make_thumbnail(image: bytes, width: int = THUMB_WIDTH, quality: int = 82) -> bytes:
    """JPEG preview ``width`` px wide (aspect kept) for page grids and the book list."""
    with Image.open(io.BytesIO(image)) as im:
        im = im.convert("RGB")
        if im.width > width:
            im = im.resize((width, max(1, round(im.height * width / im.width))),
                           Image.Resampling.LANCZOS)
        out = io.BytesIO()
        im.save(out, format="JPEG", quality=quality, optimize=True)
        return out.getvalue()
