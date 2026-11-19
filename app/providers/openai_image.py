"""Minimal OpenAI GPT Image edits provider (used only when IMAGE_PROVIDER=openai).

Plain httpx multipart call to POST /v1/images/edits - one request, no retries.
"""

from __future__ import annotations

import base64
import time

import httpx

from app.providers.base import (
    ImageResult,
    NoImageError,
    ProviderError,
    ensure_png,
    log_call,
    sniff_mime,
)
from app.providers.pricing import estimate_cost

_URL = "https://api.openai.com/v1/images/edits"
# gpt-image-2 accepts flexible sizes (edges multiple of 16); map our aspect ratios.
_SIZES = {"3:4": "1152x1536", "4:3": "1536x1152", "1:1": "1024x1024", "2:3": "1024x1536",
          "3:2": "1536x1024"}


class OpenAIImageProvider:
    name = "openai"

    def __init__(self, *, api_key: str, model: str):
        if not api_key:
            raise ProviderError("OPENAI_API_KEY is not set")
        self.api_key = api_key
        self.model = model

    async def edit(
        self,
        prompt: str,
        images: list[bytes],
        *,
        aspect_ratio: str = "3:4",
        size_hint: str | None = None,
    ) -> ImageResult:
        ext = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
        files = [
            ("image[]", (f"ref{i}.{ext[sniff_mime(img)]}", img, sniff_mime(img)))
            for i, img in enumerate(images)
        ]
        data = {
            "model": self.model,
            "prompt": prompt,
            "size": size_hint or _SIZES.get(aspect_ratio, "auto"),
            "n": "1",
            "output_format": "png",
        }
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=180) as client:
                resp = await client.post(
                    _URL,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    data=data,
                    files=files,
                )
        except httpx.HTTPError as exc:
            raise ProviderError(f"OpenAI request failed: {exc}") from exc
        duration = time.perf_counter() - started

        is_json = resp.headers.get("content-type", "").startswith("application/json")
        body = resp.json() if is_json else {}
        if resp.status_code >= 400:
            err = body.get("error") or {}
            code = err.get("code") or ""
            if code == "moderation_blocked":
                raise NoImageError(f"OpenAI blocked the request: {err.get('message')}",
                                   finish_reason=code)
            raise ProviderError(f"OpenAI API error {resp.status_code}: {err.get('message')}",
                                status=resp.status_code)

        items = body.get("data") or []
        if not items or not items[0].get("b64_json"):
            raise NoImageError("OpenAI returned no image", finish_reason="no_image")
        png = ensure_png(base64.b64decode(items[0]["b64_json"]))
        usage = body.get("usage") or {}
        in_tok = int(usage.get("input_tokens") or 0)
        out_tok = int(usage.get("output_tokens") or 0)
        result = ImageResult(
            png=png,
            model=self.model,
            input_tokens=in_tok,
            output_tokens=out_tok,
            images=1,
            est_cost_usd=estimate_cost(self.model, input_tokens=in_tok,
                                       output_image_tokens=out_tok, images=1),
            duration_s=duration,
        )
        log_call(self.name, result)
        return result
