"""Deterministic offline provider: draws a coloured placeholder PNG.

Same prompt + same inputs -> byte-identical output. Used by tests and for local
runs with IMAGE_PROVIDER=fake (no API key, no network, no cost).
"""

from __future__ import annotations

import hashlib
import io
import time

from PIL import Image, ImageDraw

from app.providers.base import ImageResult, log_call

_RATIOS = {"3:4": (480, 640), "4:3": (640, 480), "1:1": (512, 512), "2:3": (448, 672),
           "3:2": (672, 448), "9:16": (360, 640), "16:9": (640, 360)}


class FakeProvider:
    name = "fake"
    model = "fake"

    def __init__(self, model: str = "fake") -> None:
        self.model = model  # reported in ImageResult.model; the cost is always 0
        self.calls: list[dict] = []

    async def edit(
        self,
        prompt: str,
        images: list[bytes],
        *,
        aspect_ratio: str = "3:4",
        size_hint: str | None = None,
    ) -> ImageResult:
        started = time.perf_counter()
        self.calls.append({"prompt": prompt, "n_images": len(images), "aspect_ratio": aspect_ratio,
                           "model": self.model})
        h = hashlib.sha256(prompt.encode("utf-8"))
        for img in images:
            h.update(hashlib.sha256(img).digest())
        digest = h.hexdigest()

        w, hgt = _RATIOS.get(aspect_ratio, (480, 640))
        bg = tuple(int(digest[i : i + 2], 16) for i in (0, 2, 4))
        fg = tuple(255 - c for c in bg)
        im = Image.new("RGB", (w, hgt), bg)
        draw = ImageDraw.Draw(im)
        draw.rectangle([10, 10, w - 11, hgt - 11], outline=fg, width=6)
        draw.ellipse([w // 3, hgt // 4, 2 * w // 3, hgt // 4 + w // 3], outline=fg, width=5)
        draw.text((20, hgt - 60), f"FAKE {digest[:12]}", fill=fg)
        draw.text((20, hgt - 40), f"refs={len(images)} ar={aspect_ratio}", fill=fg)
        out = io.BytesIO()
        im.save(out, format="PNG")

        result = ImageResult(
            png=out.getvalue(),
            model=self.model,
            input_tokens=len(prompt.split()),
            output_tokens=0,
            images=1,
            est_cost_usd=0.0,
            duration_s=time.perf_counter() - started,
        )
        log_call(self.name, result)
        return result
