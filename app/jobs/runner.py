"""The book job: family match (+ family sheet) + hero -> pages (quality loop, concurrent,
saved as each finishes) -> PDF -> done.

Runs as an asyncio task in the web process (``JobRegistry``). The generation itself is
``app.pipeline.book.BookGenerator``; this module supplies the hooks that persist and
stream every step. Every step is written to the database before it is announced, so a
subscriber that reconnects gets the same picture from the snapshot. Every provider call
is recorded in ``api_calls`` (ok, failed or cancelled), every generation in
``page_attempts``.

Money: the book was charged before the job started (``app.api.books``). If the job ends
without a PDF - provider failure, crash, or cancellation (the user deleted the running
book, or the server shut down) - the charge is re-credited (idempotent per charge).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.api.common import provider_error
from app.config import get_settings
from app.db import repo, repo_assets
from app.db.models import DONE, FAILED, RUNNING, ApiCall, PageAttempt
from app.db.models import BookPage as BookPageRow
from app.db.session import get_sessionmaker
from app.jobs import progress
from app.jobs.events import broker
from app.logging_setup import book_id_var, user_id_var
from app.pipeline import quality
from app.pipeline.book import (
    HERO_PAGE,
    BookGenerator,
    BookPage,
    BookPlan,
    BookResult,
    CallCost,
    HeroDetails,
    HeroOutcome,
    Hooks,
)
from app.pipeline.pdf import build_book_pdf, pdf_filename
from app.pipeline.photo import make_thumbnail
from app.providers.base import ImageProvider, PhotoTraits, ProviderError, VisionGrader

log = logging.getLogger("app.jobs")


@dataclass
class JobInput:
    book_id: str
    user_id: int
    plan: BookPlan
    hero: HeroDetails
    photo: bytes
    charge_entry_id: int | None = None  # wallet charge to re-credit if the book fails
    cost_cap_usd: float = 0.0  # the tier's per-book cost cap (0 = none)


@dataclass
class Providers:
    hero: ImageProvider
    page: ImageProvider | None = None  # PAGE_IMAGE_MODEL; None = same as hero
    grader: VisionGrader | None = None  # None = no quality loop / family match


class JobRegistry:
    """Strong references to running job tasks (asyncio keeps only weak ones)."""

    def __init__(self) -> None:
        self.tasks: dict[str, asyncio.Task] = {}
        self.by_user: dict[int, str] = {}

    def user_busy(self, user_id: int) -> bool:
        return user_id in self.by_user

    def start(self, book_id: str, user_id: int, coro: Awaitable[None]) -> asyncio.Task:
        task = asyncio.ensure_future(coro)
        self.tasks[book_id] = task
        self.by_user[user_id] = book_id

        def _done(t: asyncio.Task) -> None:
            self.tasks.pop(book_id, None)
            if self.by_user.get(user_id) == book_id:
                del self.by_user[user_id]
            if not t.cancelled() and t.exception() is not None:
                log.error("job task crashed book_id=%s", book_id, exc_info=t.exception())

        task.add_done_callback(_done)
        return task

    async def wait(self, book_id: str, timeout: float = 30.0) -> None:
        task = self.tasks.get(book_id)
        if task is not None:
            await asyncio.wait_for(asyncio.shield(task), timeout)

    async def cancel(self, book_id: str) -> None:
        task = self.tasks.get(book_id)
        if task is None:
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def shutdown(self) -> None:
        tasks = list(self.tasks.values())
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


registry = JobRegistry()


async def _db(fn: Callable, *args, **kwargs):
    async with get_sessionmaker()() as s:
        return await fn(s, *args, **kwargs)


def _thumb_or_none(png: bytes | None) -> bytes | None:
    if not png:
        return None
    try:
        return make_thumbnail(png, width=160, quality=70)
    except Exception:
        log.warning("could not make attempt thumbnail")
        return None


class JobHooks(Hooks):
    """Persist + publish every pipeline step for one book."""

    def __init__(self, job: JobInput, total: int, max_attempts: int):
        self.job = job
        self.book_id = job.book_id
        self.total = total
        self.max_attempts = max_attempts
        self.pages_done = 0

    def publish(self, event: str, data: dict) -> None:
        broker.publish(self.book_id, event, data)

    async def stage(self, stage: str, done: int, *, persist: bool = True) -> None:
        if persist:
            await _db(repo.set_book_state, self.book_id, stage=stage)
            log.info("book stage=%s", stage)
        self.publish("stage", progress.stage_event(stage, done, self.total))

    async def call(self, call: CallCost) -> None:
        row = ApiCall(
            book_id=self.book_id, user_id=self.job.user_id, provider=call.provider[:32],
            model=call.model[:80], kind=call.kind[:32], page=call.page,
            est_cost_usd=call.est_cost_usd,
            input_tokens=call.input_tokens, output_tokens=call.output_tokens,
            duration_s=call.duration_s, ok=call.ok, error=call.error,
        )
        try:
            async with get_sessionmaker()() as s:
                try:
                    await repo.record_call(s, row)
                except BaseException:
                    await s.rollback()
                    raise
        except Exception:  # cost telemetry must never fail the book; cancellation propagates
            log.exception("could not record provider call kind=%s page=%s book_id=%s",
                          call.kind, call.page, self.book_id)
        if call.ok:
            log.info("provider call ok kind=%s page=%s model=%s duration=%.2fs est_cost=$%.4f",
                     call.kind, call.page, call.model, call.duration_s, call.est_cost_usd)
        else:
            log.warning("provider call failed kind=%s page=%s error=%s", call.kind, call.page,
                        call.error)

    async def family(self, variant: str, traits: PhotoTraits | None) -> None:
        await _db(repo_assets.set_family_variant, self.book_id, variant)

    async def family_sheet(self, png: bytes) -> None:
        await _db(repo_assets.set_family_sheet, self.book_id, png)

    async def attempt_phase(self, page: int, attempt: int, phase: str,
                            check: quality.Check | None) -> None:
        g = check.grade if check is not None else None
        self.publish("attempt", progress.attempt_event(
            page, attempt, self.max_attempts, phase,
            likeness=None if g is None else round(g.likeness, 3),
            fidelity=None if g is None else round(g.fidelity, 3),
            issues=check.issues if check is not None else None,
            error=phase == quality.RETRYING and check is None,
            budget_stopped=phase == quality.STOPPED,
        ))

    async def attempt_done(self, page: int, a: quality.Attempt) -> None:
        g = a.check.grade if a.check else None
        cost = (a.image.est_cost_usd if a.image else 0.0) + (g.meta.est_cost_usd if g else 0.0)
        thumb = await asyncio.to_thread(_thumb_or_none, a.png)
        row = PageAttempt(
            book_id=self.book_id, page=page, attempt=a.n, thumb_jpeg=thumb,
            likeness=g.likeness if g else None, fidelity=g.fidelity if g else None,
            has_text_artifacts=bool(g and g.has_text_artifacts), passed=a.passed,
            issues=list(a.check.issues) if a.check else [],
            zone=(a.check.placement.zone.zone if a.check and a.check.placement else ""),
            visible_characters=(list(g.visible_characters)
                                if g is not None and g.visible_characters is not None
                                else None),
            error=None if a.error is None else f"{type(a.error).__name__}: {a.error}"[:500],
            chosen=False, est_cost_usd=round(cost, 6), rescued=a.rescued,
        )
        await _db(repo_assets.save_attempt, row)

    async def budget_stopped(self, page: int, chosen: quality.Attempt) -> None:
        await _db(repo_assets.mark_budget_stopped, self.book_id, page, chosen.n)

    async def cost_stopped(self, page: int, chosen: quality.Attempt) -> None:
        await _db(repo_assets.mark_cost_stopped, self.book_id, page, chosen.n)

    async def hero_done(self, hero: HeroOutcome) -> None:
        thumb = await asyncio.to_thread(make_thumbnail, hero.png)
        await _db(repo.save_hero, self.book_id, hero.png, thumb)
        await _db(repo_assets.mark_chosen, self.book_id, HERO_PAGE, hero.chosen_attempt)
        await _db(repo.set_book_cost, self.book_id)
        self.publish("hero", progress.hero_event(self.book_id, hero.attempts, hero.scores,
                                                 hero.budget_stopped))

    async def page_done(self, bp: BookPage) -> None:
        thumb = await asyncio.to_thread(make_thumbnail, bp.png)
        row = BookPageRow(
            book_id=self.book_id, page=bp.page, title=bp.title, caption=bp.caption,
            text=bp.text, layout=bp.layout, zone=bp.zone.as_dict(), png=bp.png,
            thumb_jpeg=thumb, attempts=bp.attempts, status=DONE,
            portrait=bp.portrait if bp.portrait_png is not None else None,
            portrait_png=bp.portrait_png,
        )
        await _db(repo.save_page, row)
        await _db(repo_assets.mark_chosen, self.book_id, bp.page, bp.chosen_attempt)
        await _db(repo.set_book_cost, self.book_id)
        self.publish("page", progress.page_event(
            self.book_id, row, bp.scores, bp.budget_stopped,
            self.job.plan.story.speaker_label(bp.page, self.job.hero.name),
            progress.speech_for(self.job.plan.story.id, bp.page, bp.text,
                                self.job.hero.name, self.job.hero.pronouns)))
        self.pages_done += 1
        await self.stage("pages", 1 + self.pages_done, persist=False)


class BookJob:
    def __init__(self, job: JobInput, providers: Providers | ImageProvider):
        if not isinstance(providers, Providers):
            providers = Providers(hero=providers)
        self.job = job
        self.providers = providers
        self.book_id = job.book_id
        self.timings: dict[str, float] = {}

    @property
    def total(self) -> int:
        """Pictures in the book: hero + pages."""
        return 1 + len(self.job.plan.pages)

    def publish(self, event: str, data: dict) -> None:
        broker.publish(self.book_id, event, data)

    async def pdf_step(self, hooks: JobHooks, book: BookResult) -> str:
        t0 = time.perf_counter()
        await hooks.stage("binding", self.total)
        pdf = await asyncio.to_thread(build_book_pdf, book)
        filename = pdf_filename(book.story.title)
        await _db(repo.save_pdf, self.book_id, pdf, filename)
        self.timings["binding"] = round(time.perf_counter() - t0, 3)
        log.info("pdf built bytes=%d pages=%d duration=%.2fs", len(pdf), len(book.pages) + 2,
                 self.timings["binding"])
        return filename

    async def run(self) -> None:
        book_id_var.set(self.book_id)
        user_id_var.set(self.job.user_id)
        started = time.perf_counter()
        started_mono = time.monotonic()  # the quality-loop time budget counts from here
        plan = self.job.plan
        log.info("book job start story=%s pages=%s", plan.story.id, [p.page for p in plan.pages])
        gen = BookGenerator(
            plan, self.job.hero, self.job.photo, hero_provider=self.providers.hero,
            page_provider=self.providers.page, grader=self.providers.grader,
            assets=DbAssets(plan.story.id, plan.preset), started_at=started_mono,
            cost_cap_usd=self.job.cost_cap_usd,
        )
        hooks = JobHooks(self.job, self.total, gen.max_attempts)
        gen.hooks = hooks
        try:
            await _db(repo.set_book_state, self.book_id, status=RUNNING)
            book = await gen.run()
            self.timings.update(book.timings)
            if book.cost_skips:
                await _db(repo_assets.set_cost_skips, self.book_id, book.cost_skips)
            await self.pdf_step(hooks, book)
            cost = await _db(repo.set_book_cost, self.book_id)
            await _db(repo.set_book_state, self.book_id, status=DONE, stage="done",
                      finished=True)
            done_book = await _db(repo.get_book, self.book_id)
            await hooks.stage("done", self.total, persist=False)
            self.publish("done", {"pdf_url": progress.pdf_url(done_book), "preset": plan.preset})
            self.timings["total"] = round(time.perf_counter() - started, 3)
            log.info("book job done timings=%s est_cost=$%.4f", self.timings, cost)
            target = get_settings().book_time_target_s
            if self.timings["total"] > target:
                log.warning("book took %.1fs (target %.0fs) timings=%s",
                            self.timings["total"], target, self.timings)
        except asyncio.CancelledError:
            log.warning("book job cancelled")
            await self.recredit("cancelled")
            raise
        except ProviderError as exc:
            await self.fail(provider_error(exc).message)
        except Exception:
            log.exception("book job crashed")
            await self.fail(progress.GENERIC_FAILURE)

    async def fail(self, message: str) -> None:
        try:
            await _db(repo.set_book_cost, self.book_id)
            await _db(repo.set_book_state, self.book_id, status=FAILED, error=message,
                      finished=True)
        except Exception:
            log.exception("could not mark book failed")
        log.warning("book job failed: %s", message)
        await self.recredit("book failed")
        self.publish("error", {"message": message})

    async def recredit(self, reason: str) -> None:
        if self.job.charge_entry_id is None:
            return
        await recredit_charge(self.job.user_id, self.book_id, self.job.charge_entry_id, reason)


async def recredit_charge(user_id: int, book_id: str | None, charge_entry_id: int,
                          reason: str) -> bool:
    """Give a failed document's charge back (idempotent in the wallet). Never raises."""
    try:
        from app.billing import wallet

        async with get_sessionmaker()() as s:
            await wallet.recredit_document(s, user_id, book_id=book_id,
                                           charge_entry_id=charge_entry_id, reason=reason)
        log.info("re-credited charge_entry_id=%s reason=%s", charge_entry_id, reason)
        return True
    except Exception:
        log.exception("RE-CREDIT FAILED charge_entry_id=%s user_id=%s book_id=%s - fix by hand",
                      charge_entry_id, user_id, book_id)
        return False


class DbAssets:
    """Plates, bible refs and supporting-character sheets from the database (short
    sessions; bible refs cached)."""

    def __init__(self, story: str, preset: str):
        self.story = story
        self.preset = preset
        self._bible: dict[tuple[str, str], bytes] = {}
        self._lock = asyncio.Lock()

    async def plate(self, page: int) -> bytes | None:
        return await _db(repo_assets.get_plate_png, self.story, page, self.preset)

    async def characters(self, keys: list[tuple[str, str]]) -> dict[tuple[str, str], bytes]:
        async with self._lock:
            missing = [k for k in keys if k not in self._bible]
            if missing:
                self._bible.update(await _db(repo_assets.get_bible_pngs, self.story,
                                             self.preset, missing))
        return {k: self._bible[k] for k in keys if k in self._bible}

    async def sheet(self, character_id: str, costume_id: str) -> bytes | None:
        """A supporting character's sheet for ``costume_id`` ("" = plain), or None."""
        return await _db(repo_assets.get_sheet_png, self.story, self.preset, character_id,
                         costume_id)

    async def save_sheet(self, character_id: str, costume_id: str, png: bytes, prompt: str,
                         model: str) -> bytes:
        """Store a newly made sheet (no-op if another book stored one first); returns the
        stored sheet."""
        return await _db(repo_assets.insert_sheet, story=self.story,
                         character_id=character_id, costume_id=costume_id, preset=self.preset,
                         png=png, prompt=prompt, model=model[:80])

    async def portrait(self, character_id: str, costume_id: str,
                       expression: str) -> bytes | None:
        """A supporting character's stored speaker portrait, or None."""
        return await _db(repo_assets.get_portrait_png, self.story, self.preset, character_id,
                         costume_id, expression)

    async def save_portrait(self, character_id: str, costume_id: str, expression: str,
                            png: bytes, prompt: str, model: str) -> bytes:
        """Store a new speaker portrait (no-op if another book stored one first)."""
        return await _db(repo_assets.insert_portrait, story=self.story,
                         character_id=character_id, costume_id=costume_id,
                         expression=expression, preset=self.preset, png=png, prompt=prompt,
                         model=model[:80])


def start_book_job(job: JobInput, providers: Providers | ImageProvider) -> asyncio.Task:
    return registry.start(job.book_id, job.user_id, BookJob(job, providers).run())


async def recredit_failed_books() -> int:
    """Startup sweep: re-credit the charge of every FAILED book whose charge was never
    re-credited (the process died mid-job, so the job's own re-credit never ran).
    ``recredit_document`` is idempotent per charge, so running this twice is harmless.
    Returns the number of charges re-credited."""
    from sqlalchemy import select

    from app.db.models import CHARGE, RECREDIT, Book, WalletEntry

    async with get_sessionmaker()() as s:
        recredited = select(WalletEntry.charge_entry_id).where(
            WalletEntry.kind == RECREDIT, WalletEntry.charge_entry_id.is_not(None))
        q = (select(WalletEntry.id, WalletEntry.user_id, WalletEntry.book_id)
             .join(Book, Book.id == WalletEntry.book_id)
             .where(WalletEntry.kind == CHARGE, Book.status == FAILED,
                    WalletEntry.id.not_in(recredited))
             .order_by(WalletEntry.id))
        rows = (await s.execute(q)).all()
    n = 0
    for charge_id, user_id, book_id in rows:
        if await recredit_charge(user_id, book_id, charge_id, "server restarted"):
            n += 1
    if rows:
        log.warning("re-credited %d of %d charge(s) for failed books", n, len(rows))
    return n
