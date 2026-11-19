"""Price table used for COST ESTIMATES only.

These numbers are estimates copied by hand from the providers' public pricing
pages (Gemini: aistudio.google.com/docs/pricing, paid tier, checked Nov 2026).
They are not billing data. Edit freely when prices change. Unknown models fall
back to ``DEFAULT_PRICE``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelPrice:
    input_per_m: float  # USD per 1M input tokens (text + image)
    text_output_per_m: float  # USD per 1M output text/thinking tokens
    image_output_per_m: float  # USD per 1M output image tokens
    per_image: float  # USD per output image, used when image-token counts are unknown


PRICES: dict[str, ModelPrice] = {
    # Gemini image models (per_image = 1K output)
    "gemini-nano-banana-2.1": ModelPrice(1.50, 7.50, 30.00, 0.0336),
    "gemini-3.1-flash-image": ModelPrice(0.50, 3.00, 60.00, 0.067),
    "gemini-3.1-flash-lite-image": ModelPrice(0.25, 1.50, 30.00, 0.0336),
    "gemini-3-pro-image": ModelPrice(2.00, 12.00, 120.00, 0.134),
    "gemini-2.5-flash-image": ModelPrice(0.30, 2.50, 30.00, 0.039),
    # Gemini text/vision (price rises on 2027-01-01 to 1.50 / 7.50)
    "gemini-3.8-flash": ModelPrice(0.75, 3.75, 0.0, 0.0),
    # OpenAI - rough placeholder, verify against OpenAI's pricing page before relying on it
    "gpt-image-2": ModelPrice(5.00, 10.00, 40.00, 0.06),
    # Offline placeholder provider
    "fake": ModelPrice(0.0, 0.0, 0.0, 0.0),
}

DEFAULT_PRICE = ModelPrice(2.00, 12.00, 120.00, 0.134)  # pessimistic: priced like the Pro model


def price_for(model: str) -> ModelPrice:
    return PRICES.get(model, DEFAULT_PRICE)


def estimate_cost(
    model: str,
    *,
    input_tokens: int = 0,
    output_text_tokens: int = 0,
    output_image_tokens: int = 0,
    images: int = 0,
) -> float:
    """Estimated USD cost of one call. Uses per-image price when image tokens are unknown."""
    p = price_for(model)
    cost = input_tokens * p.input_per_m / 1e6 + output_text_tokens * p.text_output_per_m / 1e6
    if output_image_tokens:
        cost += output_image_tokens * p.image_output_per_m / 1e6
    else:
        cost += images * p.per_image
    return round(cost, 6)
