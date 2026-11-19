"""Gemini image provider (Nano Banana family) via the google-genai SDK.

One ``edit`` = exactly one ``generate_content`` request. SDK retries are
disabled explicitly (``HttpRetryOptions(attempts=1)``) so nothing is resent. The one
exception: when the model rejects the per-part ``media_resolution`` with a 400 that names
it, the request is repeated once without it and the model is remembered for the process
(``_NO_MEDIA_RESOLUTION``), like the grader's thinking-level fallback.

References are downscaled / re-encoded and sent with a per-part ``media_resolution``
(``app.providers.refs``) to keep input tokens low (Google's per-minute input-token quota).
"""

from __future__ import annotations

import logging
import time

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from app.providers.base import (
    ImageResult,
    NoImageError,
    ProviderError,
    aspect_off,
    ensure_png,
    image_size,
    log_call,
    provider_error_from_google,
    sniff_mime,
)
from app.providers.pricing import estimate_cost
from app.providers.refs import estimate_input_tokens, ref_levels, shrink_ref

log = logging.getLogger("app.providers.gemini")

# Image generation can take a while; one generous timeout, no retries.
_TIMEOUT_MS = 180_000


_LEVEL_ENUM = {"low": "MEDIA_RESOLUTION_LOW", "medium": "MEDIA_RESOLUTION_MEDIUM",
               "high": "MEDIA_RESOLUTION_HIGH", "ultra_high": "MEDIA_RESOLUTION_ULTRA_HIGH"}
# Image models that rejected a per-part media_resolution (400) in this process.
_NO_MEDIA_RESOLUTION: set[str] = set()


def _rejects_media_resolution(exc: genai_errors.APIError) -> bool:
    msg = f"{exc.message or ''} {exc.status or ''}".lower()
    return exc.code == 400 and ("media_resolution" in msg or "media resolution" in msg)


def _enum_name(value) -> str | None:
    if value is None:
        return None
    return getattr(value, "value", None) or str(value)


class GeminiProvider:
    name = "gemini"

    def __init__(self, *, api_key: str, model: str, image_size: str = "1K",
                 ref_max_side: int = 0, ref_level: str = "", ref_level_first: str = ""):
        """``ref_max_side`` (0 = unchanged) downscales references; ``ref_level`` /
        ``ref_level_first`` set the per-part media_resolution ('' = not set)."""
        if not api_key:
            raise ProviderError(
                "GEMINI_API_KEY is not set (use IMAGE_PROVIDER=fake for offline runs)"
            )
        self.model = model
        self.image_size = image_size
        self.ref_max_side = ref_max_side
        self.ref_level = ref_level
        self.ref_level_first = ref_level_first
        self._client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                timeout=_TIMEOUT_MS,
                retry_options=types.HttpRetryOptions(attempts=1),
            ),
        )

    async def edit(
        self,
        prompt: str,
        images: list[bytes],
        *,
        aspect_ratio: str = "3:4",
        size_hint: str | None = None,
    ) -> ImageResult:
        refs = [shrink_ref(img, self.ref_max_side) for img in images]
        levels = self._levels(len(refs))
        contents = self._contents(prompt, refs, levels)
        config = types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=types.ImageConfig(
                aspect_ratio=aspect_ratio,
                image_size=size_hint or self.image_size,
            ),
        )

        started = time.perf_counter()
        try:
            try:
                response = await self._client.aio.models.generate_content(
                    model=self.model, contents=contents, config=config
                )
            except genai_errors.APIError as exc:
                if not (any(levels) and _rejects_media_resolution(exc)):
                    raise
                log.warning("model %s rejected media_resolution (%s) - sending references "
                            "without it from now on", self.model, exc.message or exc.status)
                _NO_MEDIA_RESOLUTION.add(self.model)
                response = await self._client.aio.models.generate_content(
                    model=self.model, contents=self._contents(prompt, refs, []),
                    config=config)
        except genai_errors.APIError as exc:
            raise provider_error_from_google(exc, "Gemini API error") from exc
        duration = time.perf_counter() - started

        png = self._extract_image(response)
        log_output_size(png, aspect_ratio, refs)
        result = self._build_result(response, png, duration)
        log_call(self.name, result)
        return result

    def _levels(self, n: int) -> list[str]:
        if self.model in _NO_MEDIA_RESOLUTION:
            return ["" for _ in range(n)]
        return ref_levels(n, self.ref_level_first, self.ref_level)

    @staticmethod
    def _contents(prompt: str, refs: list[bytes], levels: list[str]) -> list:
        contents: list = [prompt]
        for i, img in enumerate(refs):
            level = levels[i] if i < len(levels) else ""
            if level:
                contents.append(types.Part.from_bytes(
                    data=img, mime_type=sniff_mime(img),
                    media_resolution=types.PartMediaResolution(
                        level=types.PartMediaResolutionLevel(_LEVEL_ENUM[level]))))
            else:
                contents.append(types.Part.from_bytes(data=img, mime_type=sniff_mime(img)))
        return contents

    def estimate_input_tokens(self, prompt: str, images: list[bytes]) -> int:
        """Estimated prompt_token_count of ``edit(prompt, images)`` (see refs.py)."""
        levels = self._levels(len(images))
        return estimate_input_tokens(prompt, images, max_side=self.ref_max_side,
                                     first_level=levels[0] if levels else "",
                                     level=levels[1] if len(levels) > 1 else "")

    def _extract_image(self, response: types.GenerateContentResponse) -> bytes:
        feedback = response.prompt_feedback
        if feedback is not None and feedback.block_reason is not None:
            reason = _enum_name(feedback.block_reason)
            raise NoImageError(
                f"Gemini blocked the prompt (block_reason={reason}"
                f"{': ' + feedback.block_reason_message if feedback.block_reason_message else ''})",
                finish_reason="SAFETY" if reason != "OTHER" else reason,
            )

        candidates = response.candidates or []
        texts: list[str] = []
        for cand in candidates:
            for part in (cand.content.parts if cand.content and cand.content.parts else []):
                blob = part.inline_data
                if blob is not None and blob.data and (blob.mime_type or "").startswith("image/"):
                    return ensure_png(blob.data)
                if part.text:
                    texts.append(part.text.strip())

        finish = _enum_name(candidates[0].finish_reason) if candidates else None
        detail = candidates[0].finish_message if candidates else None
        msg = f"Gemini returned no image (finish_reason={finish or 'none'}"
        if detail:
            msg += f", message={detail!r}"
        if texts:
            msg += f", text={' '.join(texts)[:300]!r}"
        raise NoImageError(msg + ")", finish_reason=finish)

    def _build_result(
        self, response: types.GenerateContentResponse, png: bytes, duration: float
    ) -> ImageResult:
        usage = response.usage_metadata
        input_tokens = (usage.prompt_token_count or 0) if usage else 0
        candidates_tokens = (usage.candidates_token_count or 0) if usage else 0
        thoughts = (usage.thoughts_token_count or 0) if usage else 0
        image_tokens = 0
        if usage and usage.candidates_tokens_details:
            for d in usage.candidates_tokens_details:
                if _enum_name(d.modality) == "IMAGE":
                    image_tokens += d.token_count or 0
        text_tokens = max(candidates_tokens - image_tokens, 0) + thoughts
        return ImageResult(
            png=png,
            model=response.model_version or self.model,
            input_tokens=input_tokens,
            output_tokens=candidates_tokens + thoughts,
            images=1,
            est_cost_usd=estimate_cost(
                self.model,
                input_tokens=input_tokens,
                output_text_tokens=text_tokens,
                output_image_tokens=image_tokens,
                images=1,
            ),
            duration_s=duration,
        )


def _fmt(size: tuple[int, int] | None) -> str:
    return "x".join(map(str, size)) if size else "?"


def log_output_size(png: bytes, aspect_ratio: str, refs: list[bytes]) -> bool:
    """Debug log of the raw returned image size per call (and the reference sizes). Our
    code never resizes or pads a returned picture (``ensure_png`` only re-encodes; the PDF
    cover-crops), so a size whose aspect differs from the requested one means the MODEL
    composed another shape - logged as a warning, because such pictures tend to come back
    with blank or blurred bands. Returns True when the aspect is off."""
    size = image_size(png)
    off = aspect_off(size, aspect_ratio)
    refs_txt = ",".join(_fmt(image_size(r)) for r in refs) or "-"
    if off:
        log.warning("gemini returned %s for requested aspect %s (refs %s) - check the picture "
                    "for padded / blurred bands", _fmt(size), aspect_ratio, refs_txt)
    else:
        log.debug("gemini returned %s for requested aspect %s (refs %s)", _fmt(size),
                  aspect_ratio, refs_txt)
    return off
