"""The wallet ledger: integer cents, append-only entries, per-user lock.

Other code (the book pipeline) calls ``get_balance_cents``, ``charge_document`` and
``recredit_document``. Every write commits. Balance = sum of ``amount_cents``; each entry
also stores ``balance_after_cents``, computed in the same transaction while holding a
row lock on the user (``SELECT ... FOR UPDATE`` on PostgreSQL; SQLite serialises writers
itself and ignores the clause).
"""

from __future__ import annotations

import logging

from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    CHARGE,
    R_PENDING,
    RECREDIT,
    REFUND,
    TOPUP,
    RefundRequest,
    User,
    WalletEntry,
)
from app.tiers import DEFAULT_TIER, tier_config

log = logging.getLogger("app.billing")

PRESETS_CENTS = (500, 1000, 2000)
CUSTOM_MIN_CENTS = 100
CUSTOM_MAX_CENTS = 10000
INSUFFICIENT_MESSAGE = "Not enough balance. Add funds to make a book."


class InsufficientFunds(Exception):
    def __init__(self, balance_cents: int, price_cents: int):
        super().__init__(f"balance {balance_cents} < price {price_cents}")
        self.balance_cents = balance_cents
        self.price_cents = price_cents


def insufficient_funds_response(exc: InsufficientFunds) -> JSONResponse:
    return JSONResponse(
        {"error": INSUFFICIENT_MESSAGE, "balance_cents": exc.balance_cents,
         "price_cents": exc.price_cents},
        status_code=402,
    )


def fmt_usd(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    return f"{sign}${abs(cents) // 100}.{abs(cents) % 100:02d}"


async def get_balance_cents(session: AsyncSession, user_id: int) -> int:
    q = select(func.coalesce(func.sum(WalletEntry.amount_cents), 0)).where(
        WalletEntry.user_id == user_id)
    return int((await session.execute(q)).scalar_one())


async def lock_user(session: AsyncSession, user_id: int) -> None:
    await session.execute(select(User.id).where(User.id == user_id).with_for_update())


async def _append(session: AsyncSession, user_id: int, **fields) -> WalletEntry:
    """Add an entry; caller holds the user lock. Does not commit."""
    balance = await get_balance_cents(session, user_id) + fields["amount_cents"]
    entry = WalletEntry(user_id=user_id, balance_after_cents=balance, **fields)
    session.add(entry)
    await session.flush()
    return entry


async def charge_document(session: AsyncSession, user_id: int, *, book_id: str | None,
                          description: str, price_cents: int | None = None) -> WalletEntry:
    """Debit ``price_cents`` (the chosen tier's price, ``app.tiers.tier_config``; None =
    the default tier's price). Idempotent per book_id (a second call returns the first
    charge, whatever price it is given). Raises InsufficientFunds when the balance is
    below the price."""
    price = (tier_config(DEFAULT_TIER).price_cents if price_cents is None
             else max(0, int(price_cents)))
    await lock_user(session, user_id)
    if book_id is not None:
        existing = (await session.execute(
            select(WalletEntry).where(WalletEntry.user_id == user_id,
                                      WalletEntry.kind == CHARGE,
                                      WalletEntry.book_id == book_id))).scalars().first()
        if existing is not None:
            await session.commit()  # release the lock
            return existing
    balance = await get_balance_cents(session, user_id)
    if balance < price:
        await session.rollback()
        raise InsufficientFunds(balance, price)
    entry = await _append(session, user_id, kind=CHARGE, amount_cents=-price,
                          description=description[:300], book_id=book_id)
    await session.commit()
    log.info("charge user_id=%s book_id=%s cents=%s", user_id, book_id, price)
    return entry


async def recredit_document(session: AsyncSession, user_id: int, *, book_id: str | None,
                            charge_entry_id: int, reason: str) -> WalletEntry:
    """Credit back a failed document's charge. Idempotent per charge entry."""
    await lock_user(session, user_id)
    existing = (await session.execute(
        select(WalletEntry).where(WalletEntry.user_id == user_id,
                                  WalletEntry.kind == RECREDIT,
                                  WalletEntry.charge_entry_id == charge_entry_id)
    )).scalars().first()
    if existing is not None:
        await session.commit()
        return existing
    charge = await session.get(WalletEntry, charge_entry_id)
    if charge is None or charge.user_id != user_id or charge.kind != CHARGE:
        await session.rollback()
        raise ValueError("charge entry not found for this user")
    entry = await _append(session, user_id, kind=RECREDIT, amount_cents=-charge.amount_cents,
                          description=f"Refunded: {reason}"[:300],
                          book_id=book_id if book_id is not None else charge.book_id,
                          charge_entry_id=charge_entry_id)
    await session.commit()
    log.info("recredit user_id=%s book_id=%s cents=%s", user_id, book_id, entry.amount_cents)
    return entry


async def credit_topup(session: AsyncSession, user_id: int, *, checkout_session_id: str,
                       amount_cents: int, payment_intent_id: str | None
                       ) -> tuple[WalletEntry, bool]:
    """Credit a paid Checkout Session exactly once (unique session id). Called by both the
    webhook and the success-page confirm. Returns (entry, created)."""
    await lock_user(session, user_id)
    q = select(WalletEntry).where(WalletEntry.stripe_checkout_session_id == checkout_session_id)
    existing = (await session.execute(q)).scalars().first()
    if existing is not None:
        await session.commit()
        return existing, False
    try:
        entry = await _append(
            session, user_id, kind=TOPUP, amount_cents=amount_cents,
            description=f"Added funds ({fmt_usd(amount_cents)} via Stripe)",
            stripe_checkout_session_id=checkout_session_id,
            stripe_payment_intent_id=payment_intent_id)
        await session.commit()
    except IntegrityError:
        await session.rollback()
        existing = (await session.execute(q)).scalars().first()
        if existing is None:
            raise
        return existing, False
    log.info("topup credited user_id=%s cents=%s stripe_session=%s", user_id, amount_cents,
             checkout_session_id)
    return entry, True


async def refunded_cents(session: AsyncSession, topup_id: int) -> int:
    q = select(func.coalesce(func.sum(-WalletEntry.amount_cents), 0)).where(
        WalletEntry.kind == REFUND, WalletEntry.topup_id == topup_id)
    return int((await session.execute(q)).scalar_one())


async def pending_refund_cents(session: AsyncSession, user_id: int,
                               exclude_request_id: int | None = None) -> int:
    q = select(func.coalesce(func.sum(RefundRequest.amount_cents), 0)).where(
        RefundRequest.user_id == user_id, RefundRequest.status == R_PENDING)
    if exclude_request_id is not None:
        q = q.where(RefundRequest.id != exclude_request_id)
    return int((await session.execute(q)).scalar_one())


async def refundable_cents(session: AsyncSession, topup: WalletEntry, *,
                           exclude_request_id: int | None = None) -> int:
    """min(top-up minus already refunded, balance minus other pending requests), >= 0."""
    left = topup.amount_cents - await refunded_cents(session, topup.id)
    spare = (await get_balance_cents(session, topup.user_id)
             - await pending_refund_cents(session, topup.user_id, exclude_request_id))
    return max(0, min(left, spare))
