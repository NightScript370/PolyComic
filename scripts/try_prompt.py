"""Run one real HW1 generation (hero + two scenes) from the CLI for prompt iteration.

Usage (key comes from the environment, e.g. a local .env you export yourself):
    .venv/Scripts/python scripts/try_prompt.py path/to/photo.jpg --style n64 --detail 2
    .venv/Scripts/python scripts/try_prompt.py photo.jpg --style ps1 --hero-only
    IMAGE_PROVIDER=fake .venv/Scripts/python scripts/try_prompt.py photo.jpg   # offline

Writes PNGs plus the exact prompts to tmp_out/<timestamp>-<style>-d<detail>/ (gitignored).
Makes 3 API calls (1 with --hero-only); each is billed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.logging_setup import setup_logging  # noqa: E402
from app.pipeline.hw1 import ASPECT_RATIO, STORY_ID, hw1_pages  # noqa: E402
from app.pipeline.photo import normalise_photo  # noqa: E402
from app.pipeline.prompts import hero_prompt, scene_prompt  # noqa: E402
from app.providers.base import get_image_provider  # noqa: E402
from app.stories.loader import load_story  # noqa: E402
from app.styles.presets import PRESETS, build_style_string  # noqa: E402


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("photo", type=Path)
    ap.add_argument("--style", choices=list(PRESETS), default="n64")
    ap.add_argument("--detail", type=int, choices=[1, 2, 3], default=2)
    ap.add_argument("--provider", help="override IMAGE_PROVIDER (gemini|openai|fake)")
    ap.add_argument("--hero-only", action="store_true", help="only generate the hero (1 call)")
    ap.add_argument("--hero", type=Path, help="reuse an existing hero PNG instead of generating")
    args = ap.parse_args()
    setup_logging("INFO")

    photo = normalise_photo(args.photo.read_bytes())
    provider = get_image_provider(args.provider)
    style = build_style_string(args.style, args.detail)
    story = load_story(STORY_ID)

    out = ROOT / "tmp_out" / f"{time.strftime('%Y%m%d-%H%M%S')}-{args.style}-d{args.detail}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "photo.jpg").write_bytes(photo)
    prompts: dict[str, str] = {}
    costs: dict[str, float] = {}

    if args.hero:
        hero_png = args.hero.read_bytes()
    else:
        prompts["hero"] = hero_prompt(style, story.hero_outfit)
        hero = await provider.edit(prompts["hero"], [photo], aspect_ratio=ASPECT_RATIO)
        hero_png = hero.png
        costs["hero"] = hero.est_cost_usd
    (out / "hero.png").write_bytes(hero_png)

    if not args.hero_only:
        pages = hw1_pages()
        for p in pages:
            prompts[f"page{p.page:02d}"] = scene_prompt(style, p, story)
        results = await asyncio.gather(
            *(provider.edit(prompts[f"page{p.page:02d}"], [hero_png, photo],
                            aspect_ratio=ASPECT_RATIO) for p in pages)
        )
        for p, r in zip(pages, results, strict=True):
            (out / f"page{p.page:02d}.png").write_bytes(r.png)
            costs[f"page{p.page:02d}"] = r.est_cost_usd

    for key, text in prompts.items():
        (out / f"{key}.prompt.txt").write_text(text, encoding="utf-8")
    (out / "costs.json").write_text(json.dumps(costs, indent=2), encoding="utf-8")
    print(f"saved to {out}  est total ${sum(costs.values()):.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
