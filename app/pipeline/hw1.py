"""HW1 flow: photo -> stylised hero -> two Jack-and-the-Beanstalk scenes.

Image calls: one hero edit, then two scene edits run concurrently. No retries and no
quality loop around any call (HW1 spec). With a vision grader configured, the same
family match and family reference sheet as the book run (``app.pipeline.family_sheet``):
one photo-traits call picks the family variant, one image call draws the mother in it,
and the scene with the mother ("Planting the last bean") gets the sheet as a reference.
The sheet gets the same single vision check as in the book (``family_sheet_check``:
apparent age, hair, count, no child) and at most ONE regeneration if it fails - the only
retry in HW1, because a wrong sheet (an elderly, grey-haired mother) is copied straight
into the scene. It delays only the scene with the mother (check ~2-5 s; a regeneration
~15-25 s more); the scenes themselves stay single, ungraded calls.
Like the book's hero, the hero call waits for the photo traits (~2 s) and carries the
identity line (``hero_identity_prompt``); the family sheet still runs beside the hero.
Without a grader the default variant is used, there is no sheet and no identity line.
The scene without family members does not wait for the sheet. Like book pages, scenes get
the hero reference but not the photo, and the personal items kept from the photo. The
caller picks the image model (the buyer's quality tier, ``app.tiers``) by passing ``provider``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from app.pipeline.calls import CallCost, Record, no_record, recorded_image
from app.pipeline.family import default_family_variant, kept_items_phrase
from app.pipeline.family_sheet import (
    hero_identity_prompt,
    make_family_sheet,
    match_family,
    page_family,
    sheet_names,
)
from app.pipeline.prompts import RefSpec, scene_prompt
from app.providers.base import ImageProvider, ImageResult, PhotoTraits, VisionGrader
from app.stories.loader import Page, find_page, load_story
from app.styles.presets import build_style_string

log = logging.getLogger("app.pipeline.hw1")

STORY_ID = "jack"
# (title, fallback page number) - looked up by title first; story order.
HW1_PAGES: tuple[tuple[str, int], ...] = (
    ("Climbing the beanstalk", 5),
    ("Planting the last bean", 19),
)
ASPECT_RATIO = "3:4"


@dataclass
class SceneOut:
    page: int
    title: str
    caption: str  # the scene specification sent to the model
    image: ImageResult


@dataclass
class HW1Result:
    style: str
    detail: int
    hero: ImageResult
    scenes: list[SceneOut]
    family_variant: str = ""
    traits: PhotoTraits | None = None
    family_sheet_png: bytes | None = None
    # family match / family sheet calls (the hero and scenes are reported separately)
    extra_calls: list[CallCost] = field(default_factory=list)

    @property
    def est_cost_usd(self) -> float:
        return round(self.hero.est_cost_usd + sum(s.image.est_cost_usd for s in self.scenes)
                     + sum(c.est_cost_usd for c in self.extra_calls), 4)


def hw1_pages() -> list[Page]:
    story = load_story(STORY_ID)
    return [find_page(story, title=t, page=n) for t, n in HW1_PAGES]


async def run_hw1(
    provider: ImageProvider, photo: bytes, preset: str, detail: int, *,
    grader: VisionGrader | None = None, record: Record | None = None,
) -> HW1Result:
    """``record`` receives the family match / family sheet calls as they finish (ok or
    failed), so the caller can store them even when the hero or a scene fails."""
    started = time.perf_counter()
    style = build_style_string(preset, detail)
    story = load_story(STORY_ID)
    pages = hw1_pages()
    extra: list[CallCost] = []

    async def keep(call: CallCost) -> None:
        extra.append(call)
        await (record or no_record)(call)

    async def match() -> tuple[PhotoTraits | None, str]:
        if grader is None:
            return None, default_family_variant(story)
        return await match_family(grader, photo, story, keep)  # never raises

    async def family(match_task: asyncio.Task) -> tuple[str, PhotoTraits | None, bytes | None]:
        traits, variant = await asyncio.shield(match_task)
        if grader is None:
            return variant, None, None
        sheet = None
        if any(page_family(story, p) for p in pages):
            async def call(kind, page, prompt, refs):
                return await recorded_image(provider, kind, page, prompt, refs, keep)

            sheet = await make_family_sheet(call, style, story, variant, grader=grader,
                                            record=keep)
        return variant, traits, sheet

    async def scene(p: Page, hero_png: bytes, fam: asyncio.Task, items: str) -> ImageResult:
        # Like the book: the hero reference (a child version of the person) is the only
        # identity source - no photo on scene calls.
        variant, refs = default_family_variant(story), [RefSpec("hero")]
        images = [hero_png]
        family_here = page_family(story, p)
        if family_here:  # only scenes with the mother wait for the family match + sheet
            variant, _traits, sheet = await asyncio.shield(fam)
            if sheet is not None:
                refs.append(RefSpec("family_sheet", sheet_names(family_here)))
                images.append(sheet)
        return await provider.edit(scene_prompt(style, p, story, variant, refs=refs,
                                                hero_items=items), images,
                                   aspect_ratio=ASPECT_RATIO)

    try:
        async with asyncio.TaskGroup() as tg:
            match_task = tg.create_task(match())
            fam_task = tg.create_task(family(match_task))
            # The hero waits for the photo traits (not for the family sheet), like the book.
            traits, _variant = await asyncio.shield(match_task)
            hero = await provider.edit(hero_identity_prompt(style, story, traits), [photo],
                                       aspect_ratio=ASPECT_RATIO)
            items = kept_items_phrase(traits)
            scene_tasks = [tg.create_task(scene(p, hero.png, fam_task, items)) for p in pages]
    except BaseExceptionGroup as eg:  # surface the first real failure as itself
        real = [e for e in eg.exceptions if not isinstance(e, asyncio.CancelledError)]
        raise (real or list(eg.exceptions))[0] from None

    variant, traits, sheet = fam_task.result()
    scenes = [
        SceneOut(page=p.page, title=p.title, caption=p.caption, image=t.result())
        for p, t in zip(pages, scene_tasks, strict=True)
    ]
    result = HW1Result(style=preset, detail=detail, hero=hero, scenes=scenes,
                       family_variant=variant, traits=traits, family_sheet_png=sheet,
                       extra_calls=extra)
    log.info(
        "hw1 done style=%s detail=%d variant=%s sheet=%s calls=%d duration=%.2fs "
        "est_cost=$%.4f", preset, detail, variant, sheet is not None,
        1 + len(scenes) + len(extra), time.perf_counter() - started, result.est_cost_usd,
    )
    return result
