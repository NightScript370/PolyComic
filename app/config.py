from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def normalise_database_url(url: str) -> str:
    """Render/Heroku-style ``postgres://`` / ``postgresql://`` -> ``postgresql+asyncpg://``.

    Other schemes (``postgresql+asyncpg://``, ``sqlite+aiosqlite://`` for tests) pass through.
    """
    url = (url or "").strip()
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+asyncpg://" + url[len(prefix):]
    return url


def require_database_url(url: str) -> str:
    """Return ``url`` or fail with a clear message when DATABASE_URL is not configured."""
    if not (url or "").strip():
        raise RuntimeError(
            "DATABASE_URL is not set. Copy .env.example to .env and set DATABASE_URL "
            "(e.g. postgresql+asyncpg://polycomic:<password>@localhost:5432/polycomic)."
        )
    return url


GRADER_THINKING_LEVELS = ("minimal", "low", "medium", "high")


class Settings(BaseSettings):
    """Settings read from environment only (docker/compose/Render supply them)."""

    # env_ignore_empty: an empty VAR= line (e.g. copied from .env.example) keeps the default.
    model_config = SettingsConfigDict(extra="ignore", env_ignore_empty=True)

    # No default (no credentials in code): required wherever the database is used, see
    # require_database_url(). Set DATABASE_URL in .env.
    database_url: str = ""
    session_secret: str = ""
    gemini_api_key: str = ""
    openai_api_key: str = ""
    # Image provider: gemini | openai | fake (fake = offline placeholder PNGs, no key).
    image_provider: str = "gemini"
    # Model ids are not secrets. Verified against Google's model list (Nov 2026):
    # "gemini-3-pro-image" = Nano Banana Pro (project default),
    # "gemini-nano-banana-2.1" = Nano Banana 2.1 (cheaper),
    # "gemini-3.1-flash-image" = Nano Banana 2.
    gemini_image_model: str = "gemini-3-pro-image"
    gemini_vision_model: str = "gemini-3.8-flash"
    # Output resolution tier for Gemini image models: 1K | 2K | 4K (uppercase K).
    gemini_image_size: str = "1K"
    openai_image_model: str = "gpt-image-2"
    google_client_id: str = ""
    google_client_secret: str = ""
    # Port the app listens on (run scripts / Dockerfile read PORT; keep PUBLIC_BASE_URL in sync).
    port: int = 7659
    public_base_url: str = "http://localhost:7659"
    stripe_secret_key: str = ""
    stripe_publishable_key: str = ""
    stripe_webhook_secret: str = ""
    admin_emails: str = ""
    # Quality tiers (app/tiers.py), chosen by the buyer per book / Quick Scenes run.
    # Professional: PROFESSIONAL_IMAGE_MODEL for hero, family sheet and pages; empty = the
    # old behaviour (hero + family sheet GEMINI_IMAGE_MODEL, pages PAGE_IMAGE_MODEL).
    professional_image_model: str = ""
    # Cost-Efficient: ONE cheaper model for hero, family sheet and pages (A/B candidates:
    # gemini-nano-banana-2.1, gemini-3.1-flash-lite-image; both priced in pricing.py).
    cost_efficient_image_model: str = "gemini-3.1-flash-lite-image"
    price_professional_cents: int = 399
    price_cost_efficient_cents: int = 99
    # Legacy single price. Unset (default) = per-tier prices above. Set explicitly (e.g.
    # BOOK_PRICE_CENTS=0 for free local testing) it overrides BOTH tier prices.
    book_price_cents: int | None = None
    log_level: str = "INFO"
    # Local testing only: POST /auth/dev-login signs in without Google. Ignored (route
    # disabled) when PUBLIC_BASE_URL is https. Never enable on a public server.
    dev_login: bool = False
    # Story pages generated per book: "all" (default, every page of the story) or a
    # comma-separated list / ranges for cheap test runs, e.g. "1,2,3" or "1-3,20".
    book_pages: str = "all"

    # --- generation pipeline (M4) ---------------------------------------------------
    # Story-page image model (hero uses GEMINI_IMAGE_MODEL). Empty = GEMINI_IMAGE_MODEL.
    page_image_model: str = ""
    # Model used by scripts/pregenerate_assets.py (bible + plates). Empty = GEMINI_IMAGE_MODEL.
    pregen_image_model: str = ""
    pregen_concurrency: int = 4
    # Max image-provider calls in flight in this process (all books together).
    image_concurrency: int = 20
    # Optional cap on image calls STARTED per minute in this process (all books together),
    # for a provider per-minute request quota. 0 = no cap (only IMAGE_CONCURRENCY).
    image_max_per_minute: int = 0
    # A 429 rate limit (per-minute quota, not a zero/daily quota) on an image call is
    # retried after the server's retry delay (or exponential backoff with jitter), at most
    # this many times per call and never past QUALITY_HARD_CAP_S. 0 = no such retries.
    image_rate_limit_retries: int = 3
    # Max reference images per call. 0 = provider default (see app/providers/base.py).
    image_max_refs: int = 0
    # Token-aware per-minute limiter for image calls (all books in this process): each
    # call's input tokens (prompt + references) are estimated, corrected with the usage
    # the API reports, and calls queue briefly instead of hitting Google's
    # GenerateContentPaidTierInputTokensPerModelPerMinute 429 (100 000 on our key).
    # 0 = off.
    image_input_tokens_per_minute: int = 90000
    # Reference images are downscaled to this longest side and re-encoded as JPEG before
    # an image call (0 = send them unchanged). See app/providers/refs.py.
    image_ref_max_side: int = 768
    # Gemini per-reference media_resolution (low | medium | high | ultra_high; empty =
    # not set, the API default). The first reference is the identity anchor (the hero
    # reference on pages, the photo on the hero call).
    image_ref_media_resolution: str = "medium"
    image_ref_media_resolution_first: str = "high"
    # Quality loop: generations per picture (1 = no regeneration) and pass thresholds.
    quality_max_attempts: int = 3
    # Likeness thresholds: pages (QUALITY_MIN_LIKENESS) and the hero reference
    # (QUALITY_MIN_LIKENESS_HERO). Identity mismatches (skin tone, hair colour, hair type)
    # fail regardless of the likeness number.
    quality_min_likeness: float = 0.6
    quality_min_likeness_hero: float = 0.6
    quality_min_fidelity: float = 0.6
    # Text placement ignores "key object" boxes larger than this share of the image (they
    # are regions such as a garden or the sky, not objects).
    object_max_area: float = 0.30
    # Quality-loop time budget, in seconds from the start of the book job. Once it has
    # passed, a picture that fails its check starts no further generation (the best
    # attempt so far is kept; a generation already running finishes). The hero always
    # gets at least 2 generations. 0 = no budget (always up to QUALITY_MAX_ATTEMPTS).
    quality_time_budget_s: float = 45.0
    # Hard cap on the book's generation time (seconds from the job start). No retry
    # generation (2nd/3rd attempt, the definite-error extra after the budget, a family /
    # cast sheet regeneration) and no rate-limit wait starts unless it is expected to END
    # (image call + grade, learned from this book's calls) before the cap; best attempts
    # are kept. Only a picture that has no image at all yet may go past it (the book
    # needs one). A picture whose best attempt has a DEFINITE error (contradiction /
    # duplicate, blank or blurred band, identity mismatch) may make ONE more generation
    # after the budget under the same rule. 0 = no cap (and no such exception).
    quality_hard_cap_s: float = 70.0
    # Whole-book target: a book job that takes longer logs a warning.
    book_time_target_s: float = 60.0
    # Vision grader: thinking level (minimal | low | medium | high; google-genai 3.0
    # ThinkingLevel). A model that rejects "minimal" falls back to "low" once per process.
    grader_thinking_level: str = "low"
    # The grader gets the picture and its references downscaled to this longest side
    # (JPEG) when they are larger; 0 = send them unchanged.
    grader_image_max_side: int = 768
    # Grader per-image media_resolution (low | medium | high | ultra_high; empty = API
    # default, high = 1120 tokens per image on Gemini 3). FIRST = the picture under check;
    # the other one is every reference (hero, photo, family / cast sheets). A model that
    # rejects it is graded without it (once per process).
    grader_media_resolution: str = "low"
    grader_media_resolution_first: str = "medium"
    # Per-book cost cap (estimated USD, from app.providers.pricing) per tier. Optional calls
    # (a retry generation, the definite-error extra, a family sheet regeneration, a speaker
    # portrait or its regeneration, a rate-limit re-try of an optional call) are skipped
    # when the book's running cost plus the call's expected cost (image + its check) would
    # pass it; required calls (hero, each page's first picture and grade) always run.
    # First-time shared supporting-character sheets are excluded. 0 = no cap.
    cost_cap_cost_efficient_usd: float = 0.97
    cost_cap_professional_usd: float = 3.70

    # --- retry phase and free strip rescue (app.pipeline.book / app.pipeline.rescue) ---
    # Retry phase: every page's FIRST attempt (picture + band check + grade) finishes
    # before any page retry starts; the retries then share what is left of the cost cap
    # and the hard cap in priority order (definite errors, worst first; portraits; soft
    # issues). false = the old per-page loop (each page retries as soon as it fails).
    quality_retry_phase: bool = True
    # During the first pass, a page whose attempt failed a LOCAL band check (no grade
    # needed; the free rescue failed or was not possible) may start its retry at once,
    # but only when the cost projection with this book's actual call costs stays under
    # the cap and the token limiter has no queue.
    quality_early_band_retry: bool = True
    # Free strip rescue: a padded strip (blank / blurred / seam) along an edge is cropped
    # away, the picture re-cropped to 3:4 and upscaled, re-checked and graded (one grade
    # call, no image call). Only when the strip is at most STRIP_RESCUE_MAX_DEPTH of the
    # height (width for side strips) and the aspect crop removes at most
    # STRIP_RESCUE_MAX_CROP of the other side; else a normal paid retry.
    strip_rescue: bool = True
    strip_rescue_max_depth: float = 0.22
    strip_rescue_max_crop: float = 0.30

    @field_validator("grader_thinking_level")
    @classmethod
    def _thinking_level(cls, v: str) -> str:
        v = (v or "").strip().lower()
        if v not in GRADER_THINKING_LEVELS:
            raise ValueError(f"GRADER_THINKING_LEVEL must be one of {GRADER_THINKING_LEVELS}")
        return v

    @field_validator("image_ref_media_resolution", "image_ref_media_resolution_first",
                     "grader_media_resolution", "grader_media_resolution_first")
    @classmethod
    def _media_level(cls, v: str) -> str:
        from app.providers.refs import normalise_level

        return normalise_level(v)

    @field_validator("database_url")
    @classmethod
    def _normalise_db_url(cls, v: str) -> str:
        return normalise_database_url(v)

    @property
    def is_https(self) -> bool:
        return self.public_base_url.strip().lower().startswith("https://")

    @property
    def dev_login_enabled(self) -> bool:
        return self.dev_login and not self.is_https

    @property
    def dev_mode(self) -> bool:
        """Offline/dev run: a missing SESSION_SECRET is tolerated (random per process)."""
        return self.image_provider.strip().lower() == "fake" or self.dev_login_enabled

    @property
    def page_model(self) -> str:
        return self.page_image_model.strip() or self.gemini_image_model

    @property
    def pregen_model(self) -> str:
        return self.pregen_image_model.strip() or self.gemini_image_model

    def book_page_numbers(self, story_pages: int = 20) -> tuple[int, ...]:
        """Pages to generate. "all" = 1..story_pages; otherwise "1,2,3" or "1-3,20"."""
        raw = self.book_pages.strip().lower()
        if raw == "all":
            return tuple(range(1, story_pages + 1))
        try:
            pages: tuple[int, ...] = ()
            for part in (x.strip() for x in raw.split(",")):
                if not part:
                    continue
                if "-" in part:
                    a, b = (int(v) for v in part.split("-", 1))
                    if b < a:
                        raise ValueError(part)
                    pages += tuple(range(a, b + 1))
                else:
                    pages += (int(part),)
        except ValueError as exc:
            raise ValueError(
                f"BOOK_PAGES must be comma-separated numbers: {self.book_pages!r}"
            ) from exc
        if not pages or len(set(pages)) != len(pages):
            raise ValueError("BOOK_PAGES must list at least one page, without duplicates")
        return pages


@lru_cache
def get_settings() -> Settings:
    return Settings()
