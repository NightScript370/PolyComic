"""Refund requests: a user asks, an admin approves (Stripe refund + ledger) or rejects."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing import stripe_client
from app.billing.stripe_client import BillingError
from app.billing.wallet import (
    _append,
    fmt_usd,
    lock_user,
    refundable_cents,
)
from app.config import get_settings
from app.db.models import (
    R_APPROVED,
    R_FAILED,
    R_PENDING,
    R_REJECTED,
    REFUND,
    TOPUP,
    RefundRequest,
    User,
    WalletEntry,
    utcnow,
)

log = logging.getLogger("app.billing")


def is_admin(user: User | None) -> bool:
    if user is None:
        return False
    allowed = {e.strip().lower() for e in get_settings().admin_emails.split(",") if e.strip()}
    return user.email.strip().lower() in allowed


async def create_request(session: AsyncSession, user: User, topup_id: int) -> RefundRequest:
    """Raises BillingError(400) if nothing is refundable or the entry is not the user's
    top-up, (409) if a request for it is already pending."""
    await lock_user(session, user.id)
    topup = await session.get(WalletEntry, topup_id)
    if topup is None or topup.user_id != user.id or topup.kind != TOPUP:
        await session.rollback()
        raise BillingError("That top-up was not found.", 404)
    pending = (await session.execute(select(RefundRequest.id).where(
        RefundRequest.topup_entry_id == topup_id, RefundRequest.status == R_PENDING)
    )).first()
    if pending:
        await session.rollback()
        raise BillingError("A refund request for this top-up is already pending.", 409)
    amount = await refundable_cents(session, topup)
    if amount <= 0:
        await session.rollback()
        raise BillingError(
            "Nothing can be refunded for this top-up. Refunds can only return unspent balance.",
            400)
    req = RefundRequest(user_id=user.id, topup_entry_id=topup_id, amount_cents=amount)
    session.add(req)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise BillingError("A refund request for this top-up is already pending.", 409) from exc
    log.info("refund requested id=%s user_id=%s cents=%s", req.id, user.id, amount)
    return req


async def _load_pending(session: AsyncSession, request_id: int) -> RefundRequest:
    req = await session.get(RefundRequest, request_id)
    if req is None:
        raise BillingError("Refund request not found.", 404)
    return req


async def approve(session: AsyncSession, request_id: int, admin: User,
                  note: str = "") -> RefundRequest:
    stripe_client.require_billing()  # fail fast (503) without touching the request
    req = await _load_pending(session, request_id)
    await lock_user(session, req.user_id)
    await session.refresh(req)
    if req.status != R_PENDING:
        status = req.status
        await session.rollback()
        raise BillingError(f"This request is already {status}.", 409)
    topup = await session.get(WalletEntry, req.topup_entry_id)
    # Re-check at approval time: balance may have been spent since the request.
    cap = min(req.amount_cents, await refundable_cents(session, topup, exclude_request_id=req.id))
    if cap <= 0 or not topup.stripe_payment_intent_id:
        req.status, req.admin_user_id, req.decided_at = R_FAILED, admin.id, utcnow()
        req.admin_note = ("Nothing refundable any more (balance was spent)." if cap <= 0
                          else "Top-up has no Stripe payment to refund.")
        await session.commit()
        raise BillingError(req.admin_note, 400)
    try:
        refund = await stripe_client.create_refund(topup.stripe_payment_intent_id, cap, req.id)
    except BillingError as exc:
        if exc.transient:
            # Unknown/temporary outcome: stay pending so the admin can press Approve again.
            # Safe: create_refund uses idempotency key polycomic-refund-request-{id}, so if
            # the first attempt did succeed at Stripe the retry returns the same refund
            # and the ledger entry is written then.
            await session.rollback()
            raise
        req.status, req.admin_user_id, req.decided_at = R_FAILED, admin.id, utcnow()
        req.admin_note = exc.message
        await session.commit()
        raise
    refund_id = refund.get("id")
    await _append(session, req.user_id, kind=REFUND, amount_cents=-cap,
                  description=f"Refund of {fmt_usd(topup.amount_cents)} top-up to card"[:300],
                  topup_id=topup.id, stripe_payment_intent_id=topup.stripe_payment_intent_id,
                  stripe_refund_id=refund_id)
    req.status, req.amount_cents = R_APPROVED, cap
    req.admin_user_id, req.decided_at, req.stripe_refund_id = admin.id, utcnow(), refund_id
    req.admin_note = note[:500]
    await session.commit()
    log.info("refund approved id=%s cents=%s stripe_refund=%s admin_id=%s", req.id, cap,
             refund_id, admin.id)
    return req


async def reject(session: AsyncSession, request_id: int, admin: User,
                 note: str = "") -> RefundRequest:
    req = await _load_pending(session, request_id)
    if req.status != R_PENDING:
        raise BillingError(f"This request is already {req.status}.", 409)
    req.status, req.admin_user_id, req.decided_at = R_REJECTED, admin.id, utcnow()
    req.admin_note = note[:500]
    await session.commit()
    log.info("refund rejected id=%s admin_id=%s", req.id, admin.id)
    return req
