"""Time one real book end-to-end against the configured provider and print a per-stage
timing + cost table (target: images and PDF in under 60 s).

Usage (GEMINI_API_KEY and, for plates/bible, DATABASE_URL from the environment):
    .venv/Scripts/python scripts/time_book.py photo.jpg --story jack --preset n64
    .venv/Scripts/python scripts/time_book.py photo.jpg --story red --preset ps1 --pages 1-3
    # compare models: Pro for the hero, Flash for pages
    .venv/Scripts/python scripts/time_book.py photo.jpg --story jack --preset n64 \
        --image-model gemini-3-pro-image --page-model gemini-3.1-flash-image
    IMAGE_PROVIDER=fake .venv/Scripts/python scripts/time_book.py photo.jpg --no-assets

Every call is billed (a full 20-page book with grading is roughly 20-60 image calls).
Writes the PDF, hero and page PNGs and timings.json to tmp_out/time-<timestamp>/ (gitignored).
Nothing is written to the database (api_calls are not recorded for this script).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.logging_setup import setup_logging  # noqa: E402
from app.pipeline.book import (  # noqa: E402
    BookGenerator,
    HeroDetails,
    Hooks,
    NoAssets,
    plan_book,
)
from app.pipeline.pdf import build_book_pdf, pdf_filename  # noqa: E402
from app.pipeline.photo import normalise_photo  # noqa: E402
from app.providers.base import get_image_provider, get_vision_grader  # noqa: E402
from app.stories.loader import load_story  # noqa: E402
from app.styles.presets import PRESETS  # noqa: E402

BUDGET_S = 60.0


class TimingHooks(Hooks):
    def __init__(self, t0: float):
        self.t0 = t0
        self.marks: list[tuple[str, float]] = []

    def mark(self, what: str) -> None:
        self.marks.append((what, round(time.perf_counter() - self.t0, 2)))
        print(f"  +{self.marks[-1][1]:6.2f}s  {what}", flush=True)

    async def stage(self, stage, done):
        self.mark(f"stage {stage}")

    async def family(self, variant, traits):
        self.mark(f"family variant {variant}")

    async def attempt_phase(self, page, attempt, phase, check):
        if phase == "retrying":
            self.mark(f"{'hero' if page == 0 else f'page {page}'} attempt {attempt} failed "
                      "check -> retry")

    async def hero_done(self, hero):
        self.mark(f"hero ready (attempts={hero.attempts})")

    async def page_done(self, page):
        self.mark(f"page {page.page} ready (attempts={page.attempts}, zone={page.zone.zone})")


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("photo", type=Path)
    ap.add_argument("--story", choices=["jack", "red"], default="jack")
    ap.add_argument("--preset", choices=list(PRESETS), default="n64")
    ap.add_argument("--detail", type=int, choices=[1, 2, 3], default=2)
    ap.add_argument("--pages", default="all", help='"all" or e.g. "1-3,20"')
    ap.add_argument("--image-model", help="hero model (default GEMINI_IMAGE_MODEL)")
    ap.add_argument("--page-model", help="page model (default PAGE_IMAGE_MODEL)")
    ap.add_argument("--no-grader", action="store_true", help="skip family match + quality loop")
    ap.add_argument("--no-assets", action="store_true",
                    help="do not read plates/bible from the database")
    ap.add_argument("--name", default="Mia")
    ap.add_argument("--pronouns", default="she")
    args = ap.parse_args()
    setup_logging("WARNING")
    settings = get_settings()

    photo = normalise_photo(args.photo.read_bytes())
    story = load_story(args.story)
    settings.book_pages = args.pages
    pages = settings.book_page_numbers(len(story.pages))
    plan = plan_book(args.story, pages, args.preset, args.detail)
    hero_provider = get_image_provider(model=args.image_model)
    page_model = args.page_model or settings.page_model
    page_provider = (get_image_provider(model=page_model)
                     if page_model != getattr(hero_provider, "model", None) else hero_provider)
    grader = None if args.no_grader else get_vision_grader()
    if args.no_assets:
        assets = NoAssets()
    else:
        from app.jobs.runner import DbAssets

        assets = DbAssets(plan.story.id, plan.preset)

    print(f"story={args.story} preset={args.preset} pages={len(pages)} "
          f"hero_model={getattr(hero_provider, 'model', '?')} "
          f"page_model={getattr(page_provider, 'model', '?')} "
          f"grader={getattr(grader, 'model', None)} "
          f"max_attempts={settings.quality_max_attempts} "
          f"image_concurrency={settings.image_concurrency}", flush=True)

    t0 = time.perf_counter()
    hooks = TimingHooks(t0)
    gen = BookGenerator(plan, HeroDetails(name=args.name, pronouns=args.pronouns), photo,
                        hero_provider=hero_provider, page_provider=page_provider,
                        grader=grader, assets=assets, hooks=hooks)
    book = await gen.run()
    t_pdf = time.perf_counter()
    pdf = await asyncio.to_thread(build_book_pdf, book)
    timings = dict(book.timings)
    timings["binding"] = round(time.perf_counter() - t_pdf, 3)
    timings["total"] = round(time.perf_counter() - t0, 3)

    out = ROOT / "tmp_out" / f"time-{time.strftime('%Y%m%d-%H%M%S')}-{args.story}-{args.preset}"
    out.mkdir(parents=True, exist_ok=True)
    (out / pdf_filename(book.story.title)).write_bytes(pdf)
    (out / "hero.png").write_bytes(book.hero_png)
    for p in book.pages:
        (out / f"page{p.page:02d}.png").write_bytes(p.png)

    by_kind: dict[tuple[str, str], list] = defaultdict(lambda: [0, 0, 0.0, 0.0])
    for c in book.costs:
        row = by_kind[(c.kind, c.model)]
        row[0] += 1
        row[1] += 0 if c.ok else 1
        row[2] += c.est_cost_usd
        row[3] += c.duration_s

    print("\nstage            seconds")
    for k in ("family_match", "family_sheet", "hero", "pages", "images_total", "binding",
              "total"):
        if k in timings:
            print(f"  {k:<14} {timings[k]:8.2f}")
    print("\ncalls by kind    model                        n  failed   est_cost  sum_call_s")
    for (kind, model), (n, failed, cost, dur) in sorted(by_kind.items()):
        print(f"  {kind:<14} {model:<26} {n:3d}  {failed:6d}  ${cost:8.4f}  {dur:9.1f}")
    attempts = [book.hero_attempts] + [p.attempts for p in book.pages]
    print(f"\ngenerations per picture: {attempts} (total {sum(attempts)})")
    print(f"est total cost ${book.est_cost_usd:.4f}")
    verdict = "OK" if timings["total"] < BUDGET_S else "OVER BUDGET"
    print(f"wall clock {timings['total']:.1f}s - {verdict} (target < {BUDGET_S:.0f}s)")
    (out / "timings.json").write_text(json.dumps(
        {"timings": timings, "marks": hooks.marks, "attempts": attempts,
         "est_cost_usd": book.est_cost_usd,
         "calls": [c.__dict__ for c in book.costs]}, indent=2), encoding="utf-8")
    print(f"saved to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
