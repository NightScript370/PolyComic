"""HW1 API: styles list and photo -> hero + two scenes (unlisted "Quick Scenes").

Each run is charged like a document at the chosen tier's price (form field ``tier``,
``app.tiers``; default cost_efficient) before any provider call and re-credited if the
run fails. The tier also picks the image model for the hero, family sheet and scenes.
With a vision grader configured (GEMINI_API_KEY, or
IMAGE_PROVIDER=fake) the run also makes the book's family match + family sheet calls
(``app.pipeline.hw1``); without one it uses the default family variant."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, File, Form, UploadFile

from app.api.common import (
    NOT_CONFIGURED,
    SAFETY_MESSAGE,  # noqa: F401 - re-exported for callers/tests
    ApiError,
    data_url,
    error,
    parse_style,
    provider_error,
    read_photo,
)
from app.auth.deps import CurrentUser, DbSession
from app.db.models import ApiCall
from app.jobs.runner import recredit_charge
from app.pipeline.calls import CallCost
from app.pipeline.hw1 import run_hw1
from app.providers.base import (
    ImageResult,
    ProviderError,
    VisionGrader,
    get_image_provider,
    get_vision_grader,
)
from app.styles.presets import styles_for_ui
from app.tiers import parse_tier, tier_config, tier_image_provider

log = logging.getLogger("app.api.hw1")
router = APIRouter(prefix="/api")


@router.get("/styles")
async def list_styles() -> list[dict]:
    return styles_for_ui()


def _call_row(user_id: int, provider: str, kind: str, page: int | None,
              r: ImageResult | None = None, error_text: str | None = None) -> ApiCall:
    return ApiCall(
        book_id=None, user_id=user_id, provider=provider[:32],
        model=(r.model if r else "")[:80], kind=kind[:32], page=page,
        est_cost_usd=r.est_cost_usd if r else 0.0,
        input_tokens=r.input_tokens if r else 0, output_tokens=r.output_tokens if r else 0,
        duration_s=round(r.duration_s, 3) if r else 0.0, ok=error_text is None, error=error_text,
    )


def _cost_row(user_id: int, c: CallCost) -> ApiCall:
    return ApiCall(
        book_id=None, user_id=user_id, provider=c.provider[:32], model=c.model[:80],
        kind=c.kind[:32],
        page=c.page, est_cost_usd=c.est_cost_usd, input_tokens=c.input_tokens,
        output_tokens=c.output_tokens, duration_s=c.duration_s, ok=c.ok, error=c.error,
    )


async def _save_calls(session, rows: list[ApiCall]) -> None:
    """Persist the cost rows. Telemetry must never fail a run (the user already paid and
    the images exist): on any error roll back, log, and carry on. Cancellation propagates."""
    try:
        session.add_all(rows)
        await session.commit()
    except Exception:
        log.exception("could not record %d hw1 api call(s)", len(rows))
        try:
            await session.rollback()
        except Exception:
            log.exception("rollback after failed api call recording failed")


def _grader() -> VisionGrader | None:
    """The vision grader for the family match, or None (default family variant)."""
    try:
        return get_vision_grader()
    except (ProviderError, ValueError) as exc:
        log.warning("no vision grader for Quick Scenes (default family variant): %s", exc)
        return None


def _wallet():
    """The billing wallet (``app.billing.wallet``), imported lazily; tests replace this."""
    from app.billing import wallet

    return wallet


@router.post("/hw1/generate")
async def generate(
    user: CurrentUser,
    session: DbSession,
    photo: Annotated[UploadFile | None, File()] = None,
    style: Annotated[str | None, Form()] = None,
    detail: Annotated[str | None, Form()] = None,
    tier: Annotated[str | None, Form()] = None,
):
    uid = user.id  # plain int: the wallet may roll the session back (expiring ``user``)
    try:
        try:
            tier_id = parse_tier(tier)
        except ValueError as exc:
            raise ApiError(400, str(exc)) from exc
        preset, detail_n = parse_style(style, detail)
        image = await read_photo(photo)
    except ApiError as exc:
        return exc.response()

    cfg = tier_config(tier_id)
    try:
        provider = tier_image_provider(cfg.image_model, get_image_provider)
    except (ProviderError, ValueError) as exc:
        log.error("image provider unavailable: %s", exc)
        return error(503, NOT_CONFIGURED)

    wallet = _wallet()
    try:
        charge = await wallet.charge_document(
            session, uid, book_id=None,
            description=f"Quick Scenes (hero + 2 scenes, {cfg.label})",
            price_cents=cfg.price_cents)
    except wallet.InsufficientFunds as exc:
        return wallet.insufficient_funds_response(exc)
    charge_id = getattr(charge, "id", charge)

    family_calls: list[CallCost] = []

    async def keep(call: CallCost) -> None:
        family_calls.append(call)

    try:
        result = await run_hw1(provider, image, preset, detail_n, grader=_grader(),
                               record=keep)
    except ProviderError as exc:
        # Hero/scene failures are not attributed to a single call here; the family match /
        # sheet calls are recorded one by one.
        rows = [_cost_row(uid, c) for c in family_calls]
        rows.append(_call_row(uid, provider.name, "hw1", None,
                              error_text=f"{type(exc).__name__}: {exc}"[:500]))
        await _save_calls(session, rows)
        await recredit_charge(uid, None, charge_id, "Quick Scenes failed")
        return provider_error(exc).response()
    except BaseException:
        await recredit_charge(uid, None, charge_id, "Quick Scenes failed")
        raise

    rows = [_cost_row(uid, c) for c in family_calls]
    rows += [_call_row(uid, provider.name, kind, page, r)
             for kind, page, r in [("hw1_hero", None, result.hero),
                                   *[("hw1_scene", sc.page, sc.image) for sc in result.scenes]]]
    await _save_calls(session, rows)

    return {
        "style": result.style,
        "detail": result.detail,
        "tier": tier_id.value,
        "hero": {"data_url": data_url(result.hero.png)},
        "scenes": [
            {"page": s.page, "title": s.title, "caption": s.caption,
             "data_url": data_url(s.image.png)}
            for s in result.scenes
        ],
        "est_cost_usd": result.est_cost_usd,
    }
