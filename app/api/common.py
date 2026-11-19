"""Helpers shared by the generation APIs (HW1 and the book)."""

from __future__ import annotations

import base64
import logging

from fastapi import UploadFile
from fastapi.responses import JSONResponse

from app.pipeline.photo import MAX_UPLOAD_BYTES, PhotoError, normalise_photo
from app.providers.base import NoImageError, ProviderError
from app.styles.presets import DEFAULT_DETAIL, DETAIL_LEVELS, PRESETS

log = logging.getLogger("app.api")

SAFETY_MESSAGE = (
    "The image service declined to draw this picture (safety filter). "
    "Please try a different photo - a clear, well-lit face photo works best."
)
NOT_CONFIGURED = "Image generation is not configured on this server."
BUSY_MESSAGE = "The image service is busy right now. Please try again shortly."
UNAVAILABLE_MESSAGE = "Image generation isn't available right now."


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message

    def response(self) -> JSONResponse:
        return error(self.status, self.message)


def error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def data_url(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def parse_style(style: str | None, detail: str | None) -> tuple[str, int]:
    if style not in PRESETS:
        raise ApiError(400, f"style must be one of: {', '.join(PRESETS)}")
    try:
        detail_n = int(detail) if detail not in (None, "") else DEFAULT_DETAIL
    except ValueError:
        detail_n = -1
    if detail_n not in DETAIL_LEVELS:
        raise ApiError(400, "detail must be 1, 2 or 3")
    return style, detail_n


async def read_photo(photo: UploadFile | None) -> bytes:
    if photo is None:
        raise ApiError(400, "photo is required")
    raw = await photo.read(MAX_UPLOAD_BYTES + 1)
    try:
        return normalise_photo(raw)
    except PhotoError as exc:
        raise ApiError(413 if len(raw) > MAX_UPLOAD_BYTES else 400, str(exc)) from exc


def provider_error(exc: ProviderError) -> ApiError:
    """Map a provider failure to a user-facing error (no upstream details leaked)."""
    if isinstance(exc, NoImageError):
        log.warning("no image: %s", exc)
        if exc.is_safety:
            return ApiError(422, SAFETY_MESSAGE)
        return ApiError(502, "The image service returned no picture. Please try again.")
    if exc.quota_exhausted:
        log.error("Image generation is out of credit right now: the provider quota is "
                  "exhausted (prepaid balance used up, billing not enabled, free tier / "
                  "quota limit 0 or a daily quota); every call will fail until billing is "
                  "fixed: %s", exc)
        return ApiError(503, UNAVAILABLE_MESSAGE)
    log.error("provider error: %s", exc)
    if exc.status == 429:
        return ApiError(503, BUSY_MESSAGE)
    return ApiError(502, "The image service failed. Please try again.")
