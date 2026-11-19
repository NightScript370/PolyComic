"""Pre-generate the shared assets for every book: the character bible and scene plates.

Usage (DATABASE_URL and GEMINI_API_KEY from the environment):
    .venv/Scripts/python scripts/pregenerate_assets.py --story all --preset all --dry-run
    .venv/Scripts/python scripts/pregenerate_assets.py --story jack --preset n64
    .venv/Scripts/python scripts/pregenerate_assets.py --story red --preset ps1 --only plates
    .venv/Scripts/python scripts/pregenerate_assets.py --story jack --preset sprite16 --force
    .venv/Scripts/python scripts/pregenerate_assets.py --story all --preset all --only sheets \
        --dry-run

What it makes, per story x style preset (style string at the default detail level):
  * bible  - one reference sheet per character (alone, whole body, plain background);
             family characters (Mother, Grandma) once per family variant (fam_a..fam_d);
             supporting characters once per costume that has its own sheet (Red's wolf:
             plain + grandma_disguise), drawn next to a grey bar as tall as the hero child
             with the same prompt books use when they make a missing sheet themselves
             (``app.pipeline.cast_sheets``; books generate missing ones lazily, so this
             step is optional for supporting characters).
  * plates - one background plate per page from the page's ``plate_prompt``: the scene
             WITHOUT the hero child and WITHOUT family members (their look depends on the
             book's family variant, so book pages insert them from the bible). Supporting
             characters that are not family ARE painted in, using their bible sheets as
             references - so bible runs first.

``--only sheets``: ONLY the supporting-character reference sheets (every costume with its
own sheet; no family bible, no plates) - exactly what books draw lazily on demand, made the
SAME way books make them (``app.pipeline.cast_sheets.make_cast_sheet``: the same prompt,
one vision check, at most one regeneration with the check's notes, the gentle retry after
a safety refusal), recorded in ``api_calls`` as kinds ``cast_sheet`` / ``cast_sheet_check``
and queued through the process-wide image input-token limiter
(IMAGE_INPUT_TOKENS_PER_MINUTE). ``--model`` overrides the image model (books draw a
missing sheet with the book's tier model). The dry run lists every sheet it would create
(stored ones are skipped) and the estimated cost (image + check from the pricing table).
Locations (cottages, castle) have no sheets: they are fixed text props.

Rules (bible / plates): exactly ONE provider call per generated asset per run, no
retries. A failed asset is reported and the exit code is 1; run the script again to fill
the gaps (existing assets are skipped unless --force). Every call is recorded in ``api_calls`` (kind
"bible" / "plate", no book, no user). Model: PREGEN_IMAGE_MODEL (default
GEMINI_IMAGE_MODEL); concurrency: PREGEN_CONCURRENCY (default 4).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.db import repo, repo_assets  # noqa: E402
from app.db.models import ApiCall  # noqa: E402
from app.db.session import dispose_engine, get_sessionmaker  # noqa: E402
from app.pipeline.book import call_input_tokens, image_token_rate  # noqa: E402
from app.pipeline.calls import CallCost, recorded_image  # noqa: E402
from app.pipeline.cast_sheets import costume_label, make_cast_sheet  # noqa: E402
from app.pipeline.prompts import (  # noqa: E402
    RefSpec,
    bible_prompt,
    cast_sheet_prompt,
    page_characters,
    plate_prompt,
)
from app.providers.base import (  # noqa: E402
    ImageProvider,
    VisionGrader,
    get_image_provider,
    get_vision_grader,
)
from app.providers.pricing import estimate_cost, price_for  # noqa: E402
from app.stories.loader import Story, load_story  # noqa: E402
from app.styles.presets import DEFAULT_DETAIL, PRESETS, build_style_string  # noqa: E402

STORIES = ("jack", "red")
ASPECT_RATIO = "3:4"


@dataclass(frozen=True)
class Asset:
    kind: str  # bible | plate
    story: str
    preset: str
    character_id: str = ""
    variant_id: str = ""
    page: int = 0
    costume_id: str = ""

    @property
    def label(self) -> str:
        if self.kind == "bible":
            v = f"/{self.variant_id}" if self.variant_id else ""
            k = f"@{self.costume_id}" if self.costume_id else ""
            return f"bible {self.story}/{self.preset} {self.character_id}{v}{k}"
        return f"plate {self.story}/{self.preset} page {self.page}"


@dataclass
class RunReport:
    planned: list[Asset] = field(default_factory=list)
    skipped: list[Asset] = field(default_factory=list)
    generated: list[Asset] = field(default_factory=list)
    failed: list[tuple[Asset, str]] = field(default_factory=list)
    cost_usd: float = 0.0
    est_cost_usd: float = 0.0


def bible_assets(story: Story, preset: str) -> list[Asset]:
    out = []
    for c in story.characters.values():
        if c.family and c.variants:
            out += [Asset("bible", story.id, preset, c.id, v)
                    for v in story.family_variant_ids if v in c.variants]
        else:
            out += [Asset("bible", story.id, preset, c.id, costume_id=k.id if k else "")
                    for k in c.sheet_costumes()]
    return out


def sheet_assets(story: Story, preset: str) -> list[Asset]:
    """Supporting (non-family) characters' reference sheets, one per sheet costume."""
    return [a for a in bible_assets(story, preset)
            if not story.characters[a.character_id].family]


# Expected input tokens of one sheet check call (instructions + one image) for estimates.
SHEET_CHECK_IN_TOKENS = 1200
SHEET_CHECK_OUT_TOKENS = 600


def plate_assets(story: Story, preset: str) -> list[Asset]:
    return [Asset("plate", story.id, preset, page=p.page) for p in story.pages]


def all_assets(stories: Sequence[str], presets: Sequence[str], only: str | None) -> list[Asset]:
    out = []
    for sid in stories:
        story = load_story(sid)
        for preset in presets:
            if only in (None, "bible"):
                out += bible_assets(story, preset)
            if only == "sheets":
                out += sheet_assets(story, preset)
            if only in (None, "plates"):
                out += plate_assets(story, preset)
    return out


async def existing(assets: Sequence[Asset]) -> set[Asset]:
    have: set[Asset] = set()
    groups = {(a.story, a.preset) for a in assets}
    async with get_sessionmaker()() as s:
        for story, preset in groups:
            keys = await repo_assets.sheet_keys(s, story, preset)
            pages = await repo_assets.plate_pages(s, story, preset)
            for a in assets:
                if (a.story, a.preset) != (story, preset):
                    continue
                if a.kind == "bible" and (a.character_id, a.variant_id, a.costume_id) in keys:
                    have.add(a)
                if a.kind == "plate" and a.page in pages:
                    have.add(a)
    return have


async def _record(provider: ImageProvider, kind: str, page: int | None, *, model: str,
                  cost: float = 0.0, in_tok: int = 0, out_tok: int = 0, duration: float = 0.0,
                  error: str | None = None) -> None:
    async with get_sessionmaker()() as s:
        await repo.record_call(s, ApiCall(
            book_id=None, user_id=None, provider=provider.name, model=model, kind=kind,
            page=page, est_cost_usd=cost, input_tokens=in_tok, output_tokens=out_tok,
            duration_s=round(duration, 3), ok=error is None, error=error))


async def generate_one(provider: ImageProvider, a: Asset, report: RunReport) -> None:
    """Exactly one provider call. Failures are reported, never retried."""
    story = load_story(a.story)
    style = build_style_string(a.preset, DEFAULT_DETAIL)
    model = str(getattr(provider, "model", "") or "")
    refs: list[bytes] = []
    if a.kind == "bible":
        c = story.characters[a.character_id]
        if c.family:
            prompt = bible_prompt(style, c, c.variants.get(a.variant_id) if a.variant_id
                                  else None)
        else:
            prompt = cast_sheet_prompt(style, c, c.costume(a.costume_id) if a.costume_id
                                       else None)
    else:
        page = story.page(a.page)
        specs: list[RefSpec] = []
        async with get_sessionmaker()() as s:
            for c in page_characters(story, page):
                if c.family:
                    continue
                costume = c.sheet_costume_id(page.page)
                png = await repo_assets.get_sheet_png(s, a.story, a.preset, c.id, costume)
                if png is not None:
                    specs.append(RefSpec("cast", c.name, c.id, costume_label(c, costume)))
                    refs.append(png)
        prompt = plate_prompt(style, page, story, specs)
    t0 = time.perf_counter()
    try:
        img = await provider.edit(prompt, refs, aspect_ratio=ASPECT_RATIO)
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"[:500]
        await _record(provider, a.kind, a.page or None, model=model, error=err,
                      duration=time.perf_counter() - t0)
        report.failed.append((a, err))
        print(f"  FAILED {a.label}: {err}", flush=True)
        return
    await _record(provider, a.kind, a.page or None, model=img.model, cost=img.est_cost_usd,
                  in_tok=img.input_tokens, out_tok=img.output_tokens, duration=img.duration_s)
    async with get_sessionmaker()() as s:
        if a.kind == "bible":
            await repo_assets.save_bible(s, story=a.story, character_id=a.character_id,
                                         variant_id=a.variant_id, preset=a.preset, png=img.png,
                                         prompt=prompt, model=img.model,
                                         costume_id=a.costume_id)
        else:
            await repo_assets.save_plate(s, story=a.story, page=a.page, preset=a.preset,
                                         png=img.png, prompt=prompt, model=img.model)
    report.generated.append(a)
    report.cost_usd += img.est_cost_usd
    print(f"  ok {a.label} ({img.duration_s:.1f}s, ${img.est_cost_usd:.4f})", flush=True)


async def _record_cost(call: CallCost) -> None:
    async with get_sessionmaker()() as s:
        await repo.record_call(s, ApiCall(
            book_id=None, user_id=None, provider=call.provider, model=call.model,
            kind=call.kind, page=call.page, est_cost_usd=call.est_cost_usd,
            input_tokens=call.input_tokens, output_tokens=call.output_tokens,
            duration_s=call.duration_s, ok=call.ok, error=call.error))


async def limited_image(provider: ImageProvider, kind: str, page: int | None, prompt: str,
                        refs: list[bytes]):
    """One recorded image call through the process-wide input-token limiter."""
    tokens = image_token_rate()
    est = call_input_tokens(provider, prompt, refs)
    booking = await tokens.acquire(est) if tokens is not None else None
    try:
        img = await recorded_image(provider, kind, page, prompt, refs, _record_cost)
    except BaseException:
        if booking is not None:
            tokens.release(booking)
        raise
    if booking is not None:
        tokens.settle(booking, img.input_tokens)
    return img


async def generate_sheet(provider: ImageProvider, grader: VisionGrader | None, a: Asset,
                         report: RunReport) -> None:
    """A supporting-character sheet made exactly like a book makes a missing one."""
    story = load_story(a.story)
    c = story.characters[a.character_id]
    style = build_style_string(a.preset, DEFAULT_DETAIL)
    costume = c.costume(a.costume_id) if a.costume_id else None

    async def call(kind, page, prompt, refs):
        return await limited_image(provider, kind, page, prompt, refs)

    spent: list[float] = []

    async def record(cc: CallCost) -> None:
        spent.append(cc.est_cost_usd)
        await _record_cost(cc)

    made = await make_cast_sheet(call, style, c, costume, grader=grader, record=record,
                                 may_retry=lambda: True)
    if made is None:
        report.failed.append((a, "image call failed (see api_calls)"))
        print(f"  FAILED {a.label}", flush=True)
        return
    img, prompt = made
    async with get_sessionmaker()() as s:
        await repo_assets.save_bible(s, story=a.story, character_id=a.character_id,
                                     variant_id="", preset=a.preset, png=img.png,
                                     prompt=prompt, model=img.model, costume_id=a.costume_id)
    report.generated.append(a)
    report.cost_usd += img.est_cost_usd + sum(spent)
    print(f"  ok {a.label} (${img.est_cost_usd + sum(spent):.4f} incl. checks)", flush=True)


async def run(
    stories: Sequence[str],
    presets: Sequence[str],
    *,
    only: str | None = None,
    force: bool = False,
    dry_run: bool = False,
    provider: ImageProvider | None = None,
    concurrency: int | None = None,
    model: str | None = None,
    grader: VisionGrader | None = None,
) -> RunReport:
    settings = get_settings()
    report = RunReport(planned=all_assets(stories, presets, only))
    have = set() if force else await existing(report.planned)
    todo = [a for a in report.planned if a not in have]
    report.skipped = [a for a in report.planned if a in have]
    model = (model or settings.pregen_model) if provider is None else str(
        getattr(provider, "model", ""))
    per = price_for(model).per_image
    if only == "sheets":
        check = estimate_cost(settings.gemini_vision_model,
                              input_tokens=SHEET_CHECK_IN_TOKENS,
                              output_text_tokens=SHEET_CHECK_OUT_TOKENS)
        per += check
    report.est_cost_usd = round(len(todo) * per, 4)

    n_bible = sum(a.kind == "bible" for a in todo)
    print(f"assets planned={len(report.planned)} existing={len(report.skipped)} "
          f"to_generate={len(todo)} (bible={n_bible}, plates={len(todo) - n_bible}) "
          f"model={model} est_cost=${report.est_cost_usd:.2f}"
          + (" (image + one check each; a failed check adds one more image + check)"
             if only == "sheets" else ""), flush=True)
    if dry_run:
        for a in todo:
            print(f"  would create {a.label}", flush=True)
        for a in report.skipped:
            print(f"  exists, skipped {a.label}", flush=True)
    if dry_run or not todo:
        return report

    if provider is None:
        provider = get_image_provider(model=model)
    sem = asyncio.Semaphore(max(1, concurrency or settings.pregen_concurrency))

    if only == "sheets" and grader is None:
        grader = get_vision_grader()

    async def guarded(a: Asset) -> None:
        async with sem:
            if only == "sheets":
                await generate_sheet(provider, grader, a, report)
            else:
                await generate_one(provider, a, report)

    # Bible first: plates use the (non-family) bible sheets as references.
    for kind in ("bible", "plate"):
        batch = [a for a in todo if a.kind == kind]
        if batch:
            print(f"generating {len(batch)} {kind} asset(s)...", flush=True)
            await asyncio.gather(*(guarded(a) for a in batch))

    print(f"done generated={len(report.generated)} failed={len(report.failed)} "
          f"skipped={len(report.skipped)} cost=${report.cost_usd:.4f}", flush=True)
    for a, err in report.failed:
        print(f"  failed: {a.label}: {err}", flush=True)
    if report.failed:
        print("re-run the same command to fill the gaps (existing assets are skipped)")
    return report


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--story", choices=[*STORIES, "all"], required=True)
    ap.add_argument("--preset", choices=[*PRESETS, "all"], required=True)
    ap.add_argument("--only", choices=["bible", "plates", "sheets"],
                    help="sheets = supporting-character sheets only, made like books do")
    ap.add_argument("--model", help="image model (default PREGEN_IMAGE_MODEL)")
    ap.add_argument("--force", action="store_true", help="regenerate assets that exist")
    ap.add_argument("--dry-run", action="store_true", help="print counts and cost only")
    return ap.parse_args(argv)


async def main(argv: Sequence[str] | None = None,
               provider: ImageProvider | None = None,
               grader: VisionGrader | None = None) -> int:
    args = parse_args(argv)
    stories = list(STORIES) if args.story == "all" else [args.story]
    presets = list(PRESETS) if args.preset == "all" else [args.preset]
    try:
        report = await run(stories, presets, only=args.only, force=args.force,
                           dry_run=args.dry_run, provider=provider, model=args.model,
                           grader=grader)
    finally:
        if provider is None:
            await dispose_engine()
    return 1 if report.failed else 0


if __name__ == "__main__":
    from app.logging_setup import setup_logging

    setup_logging(get_settings().log_level)
    sys.exit(asyncio.run(main()))
