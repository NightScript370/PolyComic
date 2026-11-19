"""Progress event payloads, URLs, and the DB snapshot sent first on every SSE connect.

Events (SSE ``event:`` name -> JSON ``data``):
  stage    {stage, label, done, total}   stage: hero | pages | binding | done.
           done/total count PICTURES for the whole book (hero + pages), so one progress
           bar works across stages: total = 1 + number of pages.
  attempt  {page, attempt, max_attempts, phase}   live quality-loop progress, page 0 = hero.
           phase: generating | checking | retrying | stopped. A "retrying" event that
           follows a failed check also has {likeness, fidelity, issues} (scores 0..1,
           issues a list of short strings); a "retrying" after a failed generation has
           {error: true}. "stopped" (with {budget_stopped: true}, plus the scores/issues of
           the failed check when there was one) means QUALITY_TIME_BUDGET_S had passed, so
           no further generation starts and the best attempt so far is kept.
           Not replayed in the snapshot (the final hero/page events carry the outcome).
  hero     {thumb_url, image_url, attempts, scores, budget_stopped}
  page     {page, title, caption, text, speaker, speech, portrait_url, zone, thumb_url,
           image_url, attempts, scores, budget_stopped}  (speaker = name-tab text, "" =
           none; speech = the text as display runs [[text, spoken], ...] with quotation
           marks around spoken spans, [] = plain narration; portrait_url = the speaker
           portrait for the dialogue box, null = none)
           attempts = generations actually made (1..3); scores = {likeness, fidelity,
           passed} of the chosen attempt, or null when the picture was not graded;
           budget_stopped = true when the time budget stopped further generations.
  done     {pdf_url}
  error    {message}
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.db import repo, repo_assets
from app.db.models import DONE, FAILED, Book, BookPage
from app.stories.loader import display_runs, load_story

STAGE_LABELS = {
    "queued": "Waiting to start",
    "hero": "Building hero",
    "pages": "Painting pages",
    "binding": "Binding the book",
    "done": "Done",
}
TERMINAL_EVENTS = ("done", "error")
GENERIC_FAILURE = "Something went wrong while making the book. Please try again."


def book_url(book_id: str) -> str:
    return f"/api/books/{book_id}"


def events_url(book_id: str) -> str:
    return f"/api/books/{book_id}/events"


def hero_url(book_id: str) -> str:
    return f"/api/books/{book_id}/hero.png"


def hero_thumb_url(book_id: str) -> str:
    return f"/api/books/{book_id}/hero/thumb.jpg"


def page_url(book_id: str, page: int) -> str:
    return f"/api/books/{book_id}/pages/{page}.png"


def page_thumb_url(book_id: str, page: int) -> str:
    return f"/api/books/{book_id}/pages/{page}/thumb.jpg"


def portrait_url(book_id: str, page: int) -> str:
    return f"/api/books/{book_id}/pages/{page}/portrait.png"


def pdf_url(book: Book) -> str | None:
    if book.status != DONE or not book.pdf_filename:
        return None
    return f"/api/books/{book.id}/{book.pdf_filename}"


def stage_event(stage: str, done: int, total: int) -> dict:
    return {"stage": stage, "label": STAGE_LABELS.get(stage, stage), "done": done,
            "total": total}


def hero_event(book_id: str, attempts: int = 1, scores: dict | None = None,
               budget_stopped: bool = False) -> dict:
    return {"thumb_url": hero_thumb_url(book_id), "image_url": hero_url(book_id),
            "attempts": attempts, "scores": scores, "budget_stopped": budget_stopped}


def speaker_for(story_id: str, page: int, hero_name: str) -> str:
    """Name-tab text for a page ("" = none); never raises (unknown story/page -> "")."""
    try:
        return load_story(story_id).speaker_label(page, hero_name)
    except (LookupError, ValueError, OSError):
        return ""


def speech_for(story_id: str, page: int, text: str, hero_name: str,
               pronouns: str) -> list[list]:
    """The page text as display runs ``[[text, spoken], ...]`` (quotation marks around
    spoken spans), or [] when the page has no speech; never raises."""
    try:
        runs = load_story(story_id).speech_runs(page, text, hero_name, pronouns)
    except (LookupError, ValueError, OSError):
        return []
    if not any(who for _, who in runs):
        return []
    return [[t, spoken] for t, spoken in display_runs(runs)]


def page_event(book_id: str, p: BookPage, scores: dict | None = None,
               budget_stopped: bool = False, speaker: str = "",
               speech: list | None = None) -> dict:
    return {
        "speaker": speaker,
        "speech": list(speech or []),
        "portrait_url": portrait_url(book_id, p.page) if p.portrait else None,
        "page": p.page, "title": p.title, "caption": p.caption, "text": p.text,
        "zone": p.zone, "thumb_url": page_thumb_url(book_id, p.page),
        "image_url": page_url(book_id, p.page), "attempts": p.attempts, "scores": scores,
        "budget_stopped": budget_stopped,
    }


def attempt_event(page: int, attempt: int, max_attempts: int, phase: str, *,
                  likeness: float | None = None, fidelity: float | None = None,
                  issues: list[str] | None = None, error: bool = False,
                  budget_stopped: bool = False) -> dict:
    data: dict = {"page": page, "attempt": attempt, "max_attempts": max_attempts,
                  "phase": phase}
    if likeness is not None or fidelity is not None:
        data.update(likeness=likeness, fidelity=fidelity, issues=list(issues or []))
    if error:
        data["error"] = True
    if budget_stopped:
        data["budget_stopped"] = True
    return data


async def snapshot(s: AsyncSession, book: Book) -> list[tuple[str, dict]]:
    """Events that bring a fresh subscriber up to date, rebuilt from the database."""
    pages = await repo.list_pages(s, book.id)
    hero = await repo.has_hero(s, book.id)
    summary = await repo_assets.attempt_summary(s, book.id)
    total = 1 + (len(book.page_numbers or []) or len(pages))
    done = int(hero) + len(pages)
    out: list[tuple[str, dict]] = []

    stage = "done" if book.status == DONE else book.stage
    if stage == "queued":
        stage = "hero"
    if stage in STAGE_LABELS:
        out.append(("stage", stage_event(stage, done, total)))
    if hero:
        h = summary.get(0, {})
        out.append(("hero", hero_event(book.id, h.get("attempts", 1), h.get("scores"),
                                       bool(h.get("budget_stopped")))))
    for p in pages:
        info = summary.get(p.page, {})
        out.append(("page", page_event(book.id, p, info.get("scores"),
                                       bool(info.get("budget_stopped")),
                                       speaker_for(book.story, p.page, book.hero_name),
                                       speech_for(book.story, p.page, p.text,
                                                  book.hero_name, book.pronouns))))
    if book.status == DONE:
        out.append(("done", {"pdf_url": pdf_url(book), "preset": book.preset}))
    elif book.status == FAILED:
        out.append(("error", {"message": book.error or GENERIC_FAILURE}))
    return out
