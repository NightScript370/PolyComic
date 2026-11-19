"""Quality tiers, chosen by the buyer per book (and per Quick Scenes run).

``tier_config`` is the single place that turns a tier into its image model(s), price and
label. Everything else (wallet charge, image providers, /api/wallet, admin costs, UI)
goes through it.

* ``professional``: PROFESSIONAL_IMAGE_MODEL for the hero, family sheet and pages; when
  unset, the pre-tier behaviour (hero + family sheet GEMINI_IMAGE_MODEL, pages
  PAGE_IMAGE_MODEL, default gemini-3-pro-image). Price PRICE_PROFESSIONAL_CENTS (399).
* ``cost_efficient``: ONE cheaper model, COST_EFFICIENT_IMAGE_MODEL (default
  gemini-3.1-flash-lite-image), for the hero, family sheet and pages. Price
  PRICE_COST_EFFICIENT_CENTS (99).

Both tiers use the same vision grader and the same quality loop. BOOK_PRICE_CENTS, when
set explicitly, overrides BOTH prices (e.g. ``BOOK_PRICE_CENTS=0`` for free local tests).
Pre-generated bible/plate assets are shared by both tiers (they are only references).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from app.config import Settings, get_settings

if TYPE_CHECKING:
    from app.providers.base import ImageProvider


class Tier(StrEnum):
    PROFESSIONAL = "professional"
    COST_EFFICIENT = "cost_efficient"


TIERS: tuple[Tier, ...] = (Tier.PROFESSIONAL, Tier.COST_EFFICIENT)
DEFAULT_TIER = Tier.COST_EFFICIENT  # what a new book / run gets when no tier is sent
LEGACY_TIER = Tier.PROFESSIONAL  # books made before tiers existed (all used the Pro model)
LABELS = {Tier.PROFESSIONAL: "Professional", Tier.COST_EFFICIENT: "Cost-Efficient"}
# One honest line per tier for the picker (same vision check and retries in both).
DESCRIPTIONS = {Tier.PROFESSIONAL: "Top-quality art, slower",
                Tier.COST_EFFICIENT: "Good art, low price"}


@dataclass(frozen=True)
class TierConfig:
    tier: Tier
    image_model: str  # hero + family sheet (and HW1 scenes)
    page_model: str  # story pages (same as image_model unless PAGE_IMAGE_MODEL differs)
    price_cents: int
    label: str
    # Per-book cost cap (COST_CAP_*_USD; 0 = none), enforced by app.pipeline.book.
    cost_cap_usd: float = 0.0


def parse_tier(value: str | None) -> Tier:
    """Form value -> Tier. Empty/None = DEFAULT_TIER; anything unknown raises ValueError."""
    raw = (value or "").strip().lower()
    if not raw:
        return DEFAULT_TIER
    try:
        return Tier(raw)
    except ValueError:
        raise ValueError(f"tier must be one of: {', '.join(TIERS)}") from None


def tier_config(tier: Tier | str, settings: Settings | None = None) -> TierConfig:
    tier = Tier(tier)
    s = settings or get_settings()
    if tier is Tier.PROFESSIONAL:
        own = s.professional_image_model.strip()
        image_model = own or s.gemini_image_model
        page_model = own or s.page_model
        price = s.price_professional_cents
        cap = s.cost_cap_professional_usd
    else:
        image_model = page_model = s.cost_efficient_image_model.strip() or s.gemini_image_model
        price = s.price_cost_efficient_cents
        cap = s.cost_cap_cost_efficient_usd
    if s.book_price_cents is not None:  # explicit legacy override wins for every tier
        price = s.book_price_cents
    return TierConfig(tier=tier, image_model=image_model, page_model=page_model,
                      price_cents=max(0, int(price)), label=LABELS[tier],
                      cost_cap_usd=max(0.0, float(cap)))


def tier_image_provider(model: str,
                        factory: Callable[..., ImageProvider] | None = None) -> ImageProvider:
    """The configured image provider (IMAGE_PROVIDER) with the tier's model. The tier
    models are Gemini ids; with IMAGE_PROVIDER=openai the provider keeps its own
    OPENAI_IMAGE_MODEL (the tiers then differ in price only). ``factory`` defaults to
    ``app.providers.base.get_image_provider`` (callers pass their own reference so tests
    can replace it)."""
    if factory is None:
        from app.providers.base import get_image_provider as factory
    if get_settings().image_provider.strip().lower() == "openai":
        return factory()
    return factory(model=model)


def tier_options() -> list[dict]:
    """The tier picker's server-rendered options (prices from the current settings; the
    page's JS refreshes them from GET /api/wallet)."""
    out = []
    for t in TIERS:
        cfg = tier_config(t)
        c = cfg.price_cents
        out.append({"id": t.value, "name": cfg.label.upper(), "price_cents": c,
                    "price": "FREE" if c == 0 else f"${c // 100}.{c % 100:02d}",
                    "desc": DESCRIPTIONS[t], "checked": t is DEFAULT_TIER})
    return out
