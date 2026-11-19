"""Offline tests for Gemini response parsing (no network, dummy key)."""

import pytest
from google.genai import types

from app.providers.base import NoImageError
from app.providers.gemini import GeminiProvider
from tests.conftest import make_image


def _provider() -> GeminiProvider:
    return GeminiProvider(api_key="test-not-a-key", model="gemini-nano-banana-2.1")


def test_extracts_image_and_estimates_cost():
    png = make_image("PNG", size=(30, 40))
    resp = types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(
                    role="model",
                    parts=[types.Part.from_bytes(data=png, mime_type="image/png")],
                ),
                finish_reason=types.FinishReason.STOP,
            )
        ],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=1000,
            candidates_token_count=1120,
            candidates_tokens_details=[
                types.ModalityTokenCount(modality=types.MediaModality.IMAGE, token_count=1120)
            ],
        ),
    )
    p = _provider()
    out = p._extract_image(resp)
    assert out == png
    result = p._build_result(resp, out, 1.0)
    assert result.input_tokens == 1000
    assert result.est_cost_usd == pytest.approx(1000 * 1.5e-6 + 1120 * 30e-6)


def test_safety_finish_reason_raises():
    resp = types.GenerateContentResponse(
        candidates=[types.Candidate(finish_reason=types.FinishReason.IMAGE_SAFETY)]
    )
    with pytest.raises(NoImageError) as ei:
        _provider()._extract_image(resp)
    assert ei.value.is_safety
    assert "IMAGE_SAFETY" in str(ei.value)


def test_prompt_block_raises():
    resp = types.GenerateContentResponse(
        prompt_feedback=types.GenerateContentResponsePromptFeedback(
            block_reason=types.BlockedReason.SAFETY
        )
    )
    with pytest.raises(NoImageError) as ei:
        _provider()._extract_image(resp)
    assert ei.value.is_safety
