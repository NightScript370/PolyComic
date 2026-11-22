"""Book API (login required, owner only): create a book job, follow it over SSE, list,
fetch images/PDF, delete.

Other users' books answer 404 (never 403), so ids cannot be probed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
import unicodedata
from typing import Annotated

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from sse_starlette import EventSourceResponse, ServerSentEvent

from app.api.common import NOT_CONFIGURED, ApiError, error, parse_style, read_photo
from app.auth.deps import CurrentUser, DbSession
from app.billing import refunds
from app.config import get_settings
from app.db import repo, repo_assets
from app.db.models import ACTIVE_STATUSES, DONE, QUEUED, Book
from app.db.session import get_sessionmaker
from app.jobs import progress
from app.jobs.events import broker
from app.jobs.runner import JobInput, Providers, recredit_charge, registry, start_book_job
from app.pipeline.book import FAVOURITE_KEYS, HeroDetails, plan_book
from app.providers.base import ProviderError, get_image_provider, get_vision_grader
from app.stories.loader import load_story
from app.tiers import (
    DEFAULT_TIER,
    LEGACY_TIER,
    Tier,
    parse_tier,
    tier_config,
    tier_image_provider,
)

log = logging.getLogger("app.api.books")
router = APIRouter(prefix="/api/books")

STORIES = ("jack", "red")
NAME_MAX = 40
FAV_MAX = 40
ABOUT_MAX = 500
PRONOUNS = ("he", "she", "they")
NOT_FOUND = "Book not found."
BUSY = "You already have a book being made. Please wait for it to finish, then try again."
SSE_PING_S = 15
SSE_RECHECK_S = 30.0  # re-read the DB if no live event arrives (e.g. job lost)

IMG_CACHE = {"Cache-Control": "private, max-age=3600"}
NO_STORE = {"Cache-Control": "private, no-store"}


def _clean(value: str | None, field: str, max_len: int, *, required: bool = False) -> str:
    text = " ".join((value or "").split())
    if any(unicodedata.category(ch)[0] == "C" for ch in text):
        raise ApiError(400, f"{field} contains invalid characters")
    if required and not text:
        raise ApiError(400, f"{field} is required")
    if len(text) > max_len:
        raise ApiError(400, f"{field} must be at most {max_len} characters")
    return text


def _parse_age(age: str | None) -> int | None:
    if age is None or not age.strip():
        return None
    try:
        n = int(age.strip())
    except ValueError:
        n = 0
    if not 1 <= n <= 17:
        raise ApiError(400, "age must be a whole number from 1 to 17")
    return n


def _json(data, status: int = 200) -> JSONResponse:
    return JSONResponse(data, status_code=status, headers=NO_STORE)


def _summary(book: Book, has_hero: bool) -> dict:
    return {
        "book_id": book.id,
        "story": book.story,
        "title": book.title,
        "hero_name": book.hero_name,
        "status": book.status,
        "created_at": repo.iso(book.created_at),
        "cover_thumb_url": progress.hero_thumb_url(book.id) if has_hero else None,
        "pdf_url": progress.pdf_url(book),
        "est_cost_usd": book.est_cost_usd,
        "tier": book.tier or LEGACY_TIER.value,
    }


def _refund_view(book: Book, states: dict[str, dict]) -> dict:
    """Per-book refund state for the book list: ``status`` None / pending / refunded /
    denied; ``eligible`` = the owner may request one now (finished or failed, paid, not yet
    returned or decided)."""
    st = states.get(book.id) or {"status": None, "eligible": False, "amount_cents": 0}
    return {"status": st["status"], "amount_cents": st["amount_cents"],
            "eligible": bool(st["eligible"] and book.status in ("done", "failed"))}


def _wallet():
    """The billing wallet (``app.billing.wallet``), imported lazily; tests replace this."""
    from app.billing import wallet

    return wallet


def get_providers(tier: Tier | str = DEFAULT_TIER) -> Providers:
    """The tier's hero / family-sheet model and page model (``app.tiers.tier_config``)
    and the vision grader (the same for every tier). Raises ProviderError / ValueError
    when not configured."""
    cfg = tier_config(tier)
    hero = tier_image_provider(cfg.image_model, get_image_provider)
    page = (hero if cfg.page_model == cfg.image_model
            else tier_image_provider(cfg.page_model, get_image_provider))
    return Providers(hero=hero, page=page, grader=get_vision_grader())


async def _owned(session, book_id: str, user_id: int) -> Book:
    book = await repo.get_book(session, book_id, user_id)
    if book is None:
        raise ApiError(404, NOT_FOUND)
    return book


@router.post("")
async def create_book(
    user: CurrentUser,
    session: DbSession,
    photo: Annotated[UploadFile | None, File()] = None,
    story: Annotated[str | None, Form()] = None,
    style: Annotated[str | None, Form()] = None,
    detail: Annotated[str | None, Form()] = None,
    name: Annotated[str | None, Form()] = None,
    pronouns: Annotated[str | None, Form()] = None,
    age: Annotated[str | None, Form()] = None,
    fav_color: Annotated[str | None, Form()] = None,
    fav_food: Annotated[str | None, Form()] = None,
    fav_game: Annotated[str | None, Form()] = None,
    fav_animal: Annotated[str | None, Form()] = None,
    about: Annotated[str | None, Form()] = None,
    tier: Annotated[str | None, Form()] = None,
):
    requested_at = time.monotonic()  # the book's end-to-end clock starts at the request
    uid = user.id  # plain int: the wallet may roll the session back (expiring ``user``)
    try:
        tier_id = parse_tier(tier)
    except ValueError as exc:
        raise ApiError(400, str(exc)) from exc
    story_id = (story or "").strip().lower()
    if story_id not in STORIES:
        raise ApiError(400, "story must be jack or red")
    preset, detail_n = parse_style(style, detail)
    hero_name = _clean(name, "name", NAME_MAX, required=True)
    pr = (pronouns or "").strip().lower()
    if pr not in PRONOUNS:
        raise ApiError(400, "pronouns must be he, she or they")
    favourites = {
        "colour": _clean(fav_color, "fav_color", FAV_MAX),
        "food": _clean(fav_food, "fav_food", FAV_MAX),
        "game": _clean(fav_game, "fav_game", FAV_MAX),
        "animal": _clean(fav_animal, "fav_animal", FAV_MAX),
    }
    hero = HeroDetails(name=hero_name, pronouns=pr, age=_parse_age(age), favourites=favourites,
                       about=_clean(about, "about", ABOUT_MAX))
    image = await read_photo(photo)

    # One active job per user. The registry is the source of truth (single worker): an
    # active DB row without a running task is an orphan (job crashed before it could mark
    # the book failed) and must not lock the user out until the next restart.
    if registry.user_busy(uid):
        log.info("book rejected: user already has an active job")
        return error(409, BUSY)
    if await repo.count_active_books(session, uid) and not registry.user_busy(uid):
        n = await repo.fail_stale_books(session, progress.GENERIC_FAILURE, user_id=uid)
        log.warning("marked %d orphaned book job(s) failed", n)
    try:
        providers = get_providers(tier_id)
    except (ProviderError, ValueError) as exc:
        log.error("image provider unavailable: %s", exc)
        return error(503, NOT_CONFIGURED)
    try:
        n_pages = len(load_story(story_id).pages)
        plan = plan_book(story_id, get_settings().book_page_numbers(n_pages), preset, detail_n)
    except (LookupError, ValueError) as exc:
        log.error("bad BOOK_PAGES / story config: %s", exc)
        return error(500, "The book settings on this server are invalid.")
    if registry.user_busy(uid):  # a parallel request won the race during the awaits
        return error(409, BUSY)

    # Reserve the user's slot before the wallet/DB awaits so a concurrent request sees it.
    book_id = secrets.token_urlsafe(16)
    tier_cfg = tier_config(tier_id)
    registry.by_user[uid] = book_id
    try:
        wallet = _wallet()
        try:
            charge = await wallet.charge_document(
                session, uid, book_id=book_id,
                description=f"Book: {plan.story.title} ({hero.name}, {tier_cfg.label})",
                price_cents=tier_cfg.price_cents)
        except wallet.InsufficientFunds as exc:
            registry.by_user.pop(uid, None)
            log.info("book rejected: insufficient funds")
            return wallet.insufficient_funds_response(exc)
    except BaseException:
        registry.by_user.pop(uid, None)
        raise
    charge_id = getattr(charge, "id", charge)

    book = Book(
        id=book_id, user_id=uid, story=story_id, title=plan.story.title,
        preset=preset, detail=detail_n, hero_name=hero.name, pronouns=hero.pronouns,
        age=hero.age, favourites={k: favourites[k] for k, _ in FAVOURITE_KEYS},
        about=hero.about, family_variant=plan.family_variant,
        page_numbers=[p.page for p in plan.pages], status=QUEUED, stage="queued",
        est_cost_usd=0.0, pdf_filename="", photo_jpeg=image, tier=tier_id.value,
    )
    job = JobInput(book_id=book.id, user_id=uid, plan=plan, hero=hero, photo=image,
                   charge_entry_id=charge_id, cost_cap_usd=tier_cfg.cost_cap_usd,
                   requested_at=requested_at)
    try:
        await repo.create_book(session, book)
    except BaseException:
        registry.by_user.pop(uid, None)
        await recredit_charge(uid, book.id, charge_id, "book could not be created")
        raise
    start_book_job(job, providers)
    log.info("book queued book_id=%s story=%s pages=%s tier=%s model=%s", book.id, story_id,
             book.page_numbers, tier_id.value, tier_cfg.image_model)
    return _json({"book_id": book.id, "events_url": progress.events_url(book.id)}, 202)


@router.get("")
async def list_books(user: CurrentUser, session: DbSession):
    items = await repo.list_books(session, user.id)
    states = await refunds.book_refund_states(session, user.id, [i.book.id for i in items])
    return _json([{**_summary(i.book, i.has_hero), "refund": _refund_view(i.book, states)}
                  for i in items])


@router.get("/{book_id}")
async def get_book(book_id: str, user: CurrentUser, session: DbSession):
    book = await _owned(session, book_id, user.id)
    pages = await repo.list_pages(session, book.id)
    hero = await repo.has_hero(session, book.id)
    attempts = await repo_assets.attempt_summary(session, book.id)
    data = _summary(book, hero)
    data["refund"] = _refund_view(
        book, await refunds.book_refund_states(session, user.id, [book.id]))
    data.update({
        "preset": book.preset,
        "detail": book.detail,
        "pronouns": book.pronouns,
        "age": book.age,
        "favourites": book.favourites,
        "about": book.about,
        "stage": book.stage,
        "stage_label": progress.STAGE_LABELS.get(book.stage, book.stage),
        "error": book.error,
        "finished_at": repo.iso(book.finished_at),
        "page_numbers": book.page_numbers,
        "hero_url": progress.hero_url(book.id) if hero else None,
        "hero_thumb_url": progress.hero_thumb_url(book.id) if hero else None,
        "events_url": progress.events_url(book.id),
        "hero_attempts": attempts.get(0, {}).get("attempts", 1 if hero else 0),
        "hero_scores": attempts.get(0, {}).get("scores"),
        "pages": [{**progress.page_event(
                       book.id, p, attempts.get(p.page, {}).get("scores"),
                       speaker=progress.speaker_for(book.story, p.page, book.hero_name),
                       speech=progress.speech_for(book.story, p.page, p.text,
                                                  book.hero_name, book.pronouns)),
                   "status": p.status} for p in pages],
    })
    return _json(data)


@router.get("/{book_id}/events")
async def book_events(book_id: str, request: Request, user: CurrentUser, session: DbSession):
    book = await _owned(session, book_id, user.id)
    await session.close()  # do not hold a DB connection for the life of the stream
    uid = user.id

    async def stream():
        with broker.subscribe(book_id) as queue:  # subscribe BEFORE the snapshot: no gaps
            async with get_sessionmaker()() as s:
                current = await repo.get_book(s, book_id, uid) or book
                events = await progress.snapshot(s, current)
            sent_pages: set[int] = set()
            sent_hero = False
            for name, data in events:
                sent_pages.update([data["page"]] if name == "page" else [])
                sent_hero = sent_hero or name == "hero"
                yield _sse(name, data)
                if name in progress.TERMINAL_EVENTS:
                    return
            while True:
                try:
                    name, data = await asyncio.wait_for(queue.get(), SSE_RECHECK_S)
                except TimeoutError:
                    async with get_sessionmaker()() as s:
                        current = await repo.get_book(s, book_id, uid)
                        if current is None:
                            yield _sse("error", {"message": NOT_FOUND})
                            return
                        if current.status not in ACTIVE_STATUSES:
                            for n, d in await progress.snapshot(s, current):
                                if n in progress.TERMINAL_EVENTS:
                                    yield _sse(n, d)
                                    return
                    continue
                if name == "page":  # may already be in the snapshot
                    if data["page"] in sent_pages:
                        continue
                    sent_pages.add(data["page"])
                if name == "hero":
                    if sent_hero:
                        continue
                    sent_hero = True
                yield _sse(name, data)
                if name in progress.TERMINAL_EVENTS:
                    return

    return EventSourceResponse(
        stream(), ping=SSE_PING_S,
        headers={"Cache-Control": "private, no-cache", "X-Accel-Buffering": "no"},
    )


def _sse(event: str, data: dict) -> ServerSentEvent:
    return ServerSentEvent(data=json.dumps(data, ensure_ascii=False), event=event)


def _blob(data: bytes | None, media_type: str) -> Response:
    if data is None:
        raise ApiError(404, "Not found.")
    return Response(content=data, media_type=media_type, headers=IMG_CACHE)


@router.get("/{book_id}/hero.png")
async def hero_png(book_id: str, user: CurrentUser, session: DbSession):
    return _blob(await repo.get_book_blob(session, book_id, user.id, "hero_png"), "image/png")


@router.get("/{book_id}/hero/thumb.jpg")
async def hero_thumb(book_id: str, user: CurrentUser, session: DbSession):
    return _blob(await repo.get_book_blob(session, book_id, user.id, "hero_thumb"),
                 "image/jpeg")


@router.get("/{book_id}/pages/{page}.png")
async def page_png(book_id: str, page: int, user: CurrentUser, session: DbSession):
    return _blob(await repo.get_page_blob(session, book_id, user.id, page, "png"), "image/png")


@router.get("/{book_id}/pages/{page}/thumb.jpg")
async def page_thumb(book_id: str, page: int, user: CurrentUser, session: DbSession):
    return _blob(await repo.get_page_blob(session, book_id, user.id, page, "thumb"),
                 "image/jpeg")


@router.get("/{book_id}/pages/{page}/attempts/{attempt}/thumb.jpg")
async def attempt_thumb(book_id: str, page: int, attempt: int, user: CurrentUser,
                        session: DbSession):
    """Thumbnail of one generation (the live preview of a picture that is still being
    checked or retried)."""
    return _blob(await repo_assets.get_attempt_thumb(session, book_id, user.id, page, attempt),
                 "image/jpeg")


@router.get("/{book_id}/pages/{page}/portrait.png")
async def page_portrait(book_id: str, page: int, user: CurrentUser, session: DbSession):
    """The speaker portrait shown in the page's dialogue box (404 if the page has none)."""
    return _blob(await repo.get_page_blob(session, book_id, user.id, page, "portrait"),
                 "image/png")


@router.get("/{book_id}/{filename}")
async def download_pdf(book_id: str, filename: str, user: CurrentUser, session: DbSession):
    book = await _owned(session, book_id, user.id)
    if book.status != DONE or not book.pdf_filename or filename != book.pdf_filename:
        raise ApiError(404, NOT_FOUND)
    pdf = await repo.get_book_blob(session, book_id, user.id, "pdf")
    if pdf is None:
        raise ApiError(404, NOT_FOUND)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{book.pdf_filename}"',
                 **NO_STORE},
    )


@router.delete("/{book_id}")
async def delete_book(book_id: str, user: CurrentUser, session: DbSession):
    book = await _owned(session, book_id, user.id)
    await registry.cancel(book.id)  # stop paying for a book nobody wants (job re-credits)
    await repo_assets.delete_attempts(session, book.id)
    await repo.delete_book(session, book.id)
    log.info("book deleted book_id=%s", book.id)
    return _json({"ok": True})
