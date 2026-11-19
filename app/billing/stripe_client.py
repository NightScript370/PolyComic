"""Thin Stripe wrapper (TEST MODE only). One call each, run in a thread, no retries.

Keys come only from settings. A secret key that is not ``sk_test_...`` disables billing
(endpoints answer 503). Logs carry Stripe object ids, never keys.
"""

from __future__ import annotations

import asyncio
import logging

import stripe

from app.config import Settings, get_settings

log = logging.getLogger("app.billing.stripe")

TEST_KEY_PREFIX = "sk_test_"
PRODUCT_NAME = "PolyComic wallet top-up"
_logged: set[str] = set()


class BillingError(Exception):
    """A failure with a user-presentable message and the HTTP status to answer with."""

    def __init__(self, message: str, status: int = 502, *, transient: bool = False):
        super().__init__(message)
        self.message = message
        self.status = status
        # True when the outcome is unknown/temporary, so the caller may safely retry.
        self.transient = transient


def billing_status(settings: Settings | None = None) -> str | None:
    """None when billing is usable, else the reason (also logged once as an error)."""
    s = settings or get_settings()
    key = s.stripe_secret_key.strip()
    reason = None
    if not key:
        reason = "STRIPE_SECRET_KEY is not set"
    elif not key.startswith(TEST_KEY_PREFIX):
        reason = "STRIPE_SECRET_KEY is not a test-mode key (sk_test_...); billing disabled"
    if reason and reason not in _logged:
        _logged.add(reason)
        log.error("billing disabled: %s", reason)
    return reason


def require_billing() -> Settings:
    s = get_settings()
    if billing_status(s):
        raise BillingError("Payments are not available right now.", 503, transient=True)
    return s


def _plain(obj):
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    return dict(obj)


def _friendly(exc: stripe.StripeError) -> BillingError:
    log.error("stripe error type=%s code=%s request_id=%s", type(exc).__name__,
              getattr(exc, "code", None), getattr(exc, "request_id", None))
    if isinstance(exc, stripe.RateLimitError):
        return BillingError("The payment service is busy. Please try again shortly.", 503,
                            transient=True)
    if isinstance(exc, stripe.APIConnectionError):
        return BillingError("Could not reach the payment service. Please try again.", 502,
                            transient=True)
    if isinstance(exc, stripe.InvalidRequestError):
        return BillingError("The payment service rejected the request.", 502)
    return BillingError("The payment service failed. Please try again.", 502, transient=True)


async def _call(fn, **kwargs) -> dict:
    s = require_billing()
    try:
        return _plain(await asyncio.to_thread(fn, api_key=s.stripe_secret_key, **kwargs))
    except stripe.StripeError as exc:
        raise _friendly(exc) from exc


async def create_checkout_session(user_id: int, amount_cents: int) -> dict:
    base = get_settings().public_base_url.rstrip("/")
    obj = await _call(
        stripe.checkout.Session.create,
        mode="payment",
        line_items=[{
            "quantity": 1,
            "price_data": {"currency": "usd", "unit_amount": amount_cents,
                           "product_data": {"name": PRODUCT_NAME}},
        }],
        client_reference_id=str(user_id),
        metadata={"user_id": str(user_id), "purpose": "wallet_topup"},
        success_url=base + "/wallet?checkout=success&session_id={CHECKOUT_SESSION_ID}",
        cancel_url=base + "/wallet?checkout=cancel",
    )
    log.info("checkout session created id=%s user_id=%s cents=%s", obj.get("id"), user_id,
             amount_cents)
    return obj


async def retrieve_checkout_session(session_id: str) -> dict:
    return await _call(stripe.checkout.Session.retrieve, id=session_id)


async def create_refund(payment_intent_id: str, amount_cents: int,
                        refund_request_id: int) -> dict:
    obj = await _call(
        stripe.Refund.create,
        payment_intent=payment_intent_id,
        amount=amount_cents,
        metadata={"refund_request_id": str(refund_request_id)},
        idempotency_key=f"polycomic-refund-request-{refund_request_id}",
    )
    log.info("stripe refund created id=%s request_id=%s cents=%s", obj.get("id"),
             refund_request_id, amount_cents)
    return obj


def construct_event(payload: bytes, signature: str | None) -> dict:
    """Verify the webhook signature. Raises BillingError(400) when invalid."""
    s = get_settings()
    if billing_status(s) or not s.stripe_webhook_secret:
        raise BillingError("Webhooks are not configured.", 503)
    try:
        event = stripe.Webhook.construct_event(payload, signature or "",
                                               s.stripe_webhook_secret)
    except (ValueError, stripe.SignatureVerificationError) as exc:
        log.warning("stripe webhook rejected: %s", type(exc).__name__)
        raise BillingError("Invalid webhook.", 400) from exc
    return _plain(event)
