"""scripts/pregenerate_assets.py with the fake provider (no network, SQLite)."""

import importlib.util
import sys
from pathlib import Path

from sqlalchemy import select

from app.db import repo_assets
from app.db.models import ApiCall
from app.db.session import get_sessionmaker
from app.providers.base import ProviderError
from app.providers.fake import FakeProvider

_PATH = Path(__file__).resolve().parent.parent / "scripts" / "pregenerate_assets.py"
_spec = importlib.util.spec_from_file_location("pregenerate_assets", _PATH)
pregen = importlib.util.module_from_spec(_spec)
sys.modules["pregenerate_assets"] = pregen
_spec.loader.exec_module(pregen)

# jack: mother x 4 variants + 6 other characters = 10 sheets; 20 plates
JACK_BIBLE, JACK_PLATES = 10, 20


async def _calls() -> list[ApiCall]:
    async with get_sessionmaker()() as s:
        return list((await s.execute(select(ApiCall))).scalars())


def test_asset_plan_counts():
    jack = pregen.all_assets(["jack"], ["n64"], None)
    assert sum(a.kind == "bible" for a in jack) == JACK_BIBLE
    assert sum(a.kind == "plate" for a in jack) == JACK_PLATES
    # mother + grandma x4, wolf (plain + grandma_disguise), woodcutter
    red = pregen.all_assets(["red"], ["n64"], "bible")
    assert len(red) == 11 and {a.kind for a in red} == {"bible"}
    assert {a.costume_id for a in red if a.character_id == "wolf"} == {"", "grandma_disguise"}
    assert len(pregen.all_assets(["jack", "red"], ["n64", "ps1", "sprite16"], None)) == 183


async def test_dry_run_makes_no_calls(capsys):
    fake = FakeProvider()
    assert await pregen.main(["--story", "jack", "--preset", "n64", "--dry-run"], fake) == 0
    assert fake.calls == [] and await _calls() == []
    out = capsys.readouterr().out
    assert "to_generate=30" in out and "bible=10, plates=20" in out


async def test_generates_then_skips_existing():
    fake = FakeProvider()
    assert await pregen.main(["--story", "jack", "--preset", "n64"], fake) == 0
    assert len(fake.calls) == JACK_BIBLE + JACK_PLATES
    async with get_sessionmaker()() as s:
        keys = await repo_assets.bible_keys(s, "jack", "n64")
        assert ("mother", "fam_c") in keys and ("giant", "") in keys and len(keys) == 10
        assert await repo_assets.plate_pages(s, "jack", "n64") == set(range(1, 21))
    calls = await _calls()
    assert sorted({c.kind for c in calls}) == ["bible", "plate"]
    assert all(c.book_id is None and c.user_id is None and c.ok for c in calls)

    # plates: hero and Mother left out; non-family characters drawn from their bible sheet
    plate1 = next(c for c in fake.calls if "background plate" in c["prompt"]
                  and "humble cottage kitchen" in c["prompt"])
    assert "Leave out completely: the hero child, Mother." in plate1["prompt"]
    assert plate1["n_images"] == 1  # Milky-White's sheet

    again = FakeProvider()
    assert await pregen.main(["--story", "jack", "--preset", "n64"], again) == 0
    assert again.calls == []  # everything exists
    forced = FakeProvider()
    assert await pregen.main(["--story", "jack", "--preset", "n64", "--only", "bible",
                              "--force"], forced) == 0
    assert len(forced.calls) == JACK_BIBLE


async def test_failed_asset_reported_not_retried(capsys):
    class FailHen(FakeProvider):
        async def edit(self, prompt, images, *, aspect_ratio="3:4", size_hint=None):
            if "reference sheet for the hen" in prompt:
                self.calls.append({"prompt": prompt, "n_images": len(images)})
                raise ProviderError("boom", status=500)
            return await super().edit(prompt, images, aspect_ratio=aspect_ratio)

    fake = FailHen()
    assert await pregen.main(["--story", "jack", "--preset", "ps1", "--only", "bible"],
                             fake) == 1
    assert sum("the hen" in c["prompt"] for c in fake.calls) == 1  # one call, no retry
    assert "failed: bible jack/ps1 hen" in capsys.readouterr().out
    failed = [c for c in await _calls() if not c.ok]
    assert len(failed) == 1 and failed[0].kind == "bible"
    # re-run fills the gap only
    fill = FakeProvider()
    assert await pregen.main(["--story", "jack", "--preset", "ps1", "--only", "bible"],
                             fill) == 0
    assert len(fill.calls) == 1


async def test_sheets_mode_dry_run_lists_supporting_sheets_only(capsys):
    fake = FakeProvider()
    argv = ["--story", "all", "--preset", "all", "--only", "sheets", "--dry-run"]
    assert await pregen.main(argv, fake) == 0
    assert fake.calls == [] and await _calls() == []
    out = capsys.readouterr().out
    # jack: 6 supporting characters; red: wolf plain + grandma_disguise, woodcutter; x3 presets
    assert "to_generate=27" in out
    assert "would create bible red/ps1 wolf@grandma_disguise" in out
    assert "mother" not in out and "plate" not in out.split("to_generate")[1].split("\n")[1]


async def test_sheets_mode_makes_sheets_like_books_and_skips_stored_ones(capsys):
    from app.providers.fake_grader import FakeGrader

    fake, grader = FakeProvider(), FakeGrader()
    argv = ["--story", "red", "--preset", "n64", "--only", "sheets"]
    assert await pregen.main(argv, fake, grader) == 0
    assert len(fake.calls) == 3 and len(grader.cast_sheet_calls) == 3  # checked like books
    assert all("vertical grey" in c["prompt"] for c in fake.calls)
    kinds = sorted(c.kind for c in await _calls())
    assert kinds == ["cast_sheet"] * 3 + ["cast_sheet_check"] * 3
    async with get_sessionmaker()() as s:
        assert await repo_assets.get_sheet_png(s, "red", "n64", "wolf", "grandma_disguise")
    again = FakeProvider()
    assert await pregen.main(argv + ["--dry-run"], again) == 0
    assert "to_generate=0" in capsys.readouterr().out
