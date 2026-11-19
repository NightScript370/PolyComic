"""Recorded provider calls (cost/latency rows for ``api_calls``), shared by the book
pipeline and HW1."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.providers.base import CallMeta, ImageProvider, ImageResult

log = logging.getLogger("app.pipeline.calls")

ASPECT_RATIO = "3:4"

# Every ``CallCost.kind`` written to ``api_calls.kind`` (VARCHAR(32)); a test keeps this in
# step with the code and the column.
CALL_KINDS = ("hero", "page", "grade", "rescue_grade", "family_match", "family_sheet",
              "family_sheet_check", "cast_sheet", "cast_sheet_check", "portrait",
              "portrait_check", "hw1_hero", "hw1_scene", "hw1")


@dataclass
class CallCost:
    """One provider call (image, grade, family match, family sheet), ok/failed/cancelled."""

    # hero | page | grade | rescue_grade (grade of a free strip rescue) |
    # family_match | family_sheet | family_sheet_check | cast_sheet |
    # cast_sheet_check | portrait | portrait_check | hw1_hero | hw1_scene
    kind: str
    page: int | None
    model: str
    est_cost_usd: float
    duration_s: float
    input_tokens: int
    output_tokens: int
    provider: str = ""
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    @classmethod
    def of(cls, kind: str, page: int | None, r: ImageResult, provider: str = "") -> CallCost:
        return cls(kind, page, r.model, r.est_cost_usd, round(r.duration_s, 3),
                   r.input_tokens, r.output_tokens, provider)

    @classmethod
    def of_meta(cls, kind: str, page: int | None, m: CallMeta, provider: str = "") -> CallCost:
        return cls(kind, page, m.model, m.est_cost_usd, round(m.duration_s, 3),
                   m.input_tokens, m.output_tokens, provider)

    @classmethod
    def failed(cls, kind: str, page: int | None, model: str, started: float,
               provider: str, error: str) -> CallCost:
        return cls(kind, page, model, 0.0, round(time.perf_counter() - started, 3), 0, 0,
                   provider, error)


Record = Callable[[CallCost], Awaitable[None]]


async def no_record(call: CallCost) -> None:
    return None


def short_error(exc: BaseException) -> str:
    """Error text for api_calls (exception type + message, truncated, no payloads)."""
    return f"{type(exc).__name__}: {exc}"[:500]


async def recorded_image(provider: ImageProvider, kind: str, page: int | None, prompt: str,
                         refs: list[bytes], record: Record) -> ImageResult:
    """One image call, recorded ok / failed / cancelled. Raises what the provider raised."""
    model = str(getattr(provider, "model", "") or "")
    t0 = time.perf_counter()
    try:
        img = await provider.edit(prompt, refs, aspect_ratio=ASPECT_RATIO)
    except asyncio.CancelledError:
        try:
            await record(CallCost.failed(kind, page, model, t0, provider.name, "cancelled"))
        except Exception:  # never mask the cancellation
            log.exception("could not record cancelled call kind=%s page=%s", kind, page)
        raise
    except Exception as exc:
        await record(CallCost.failed(kind, page, model, t0, provider.name, short_error(exc)))
        raise
    await record(CallCost.of(kind, page, img, provider.name))
    return img
