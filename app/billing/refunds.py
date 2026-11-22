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
    BOOK_REFUND,
    CHARGE,
    DONE,
    FAILED,
    R_APPROVED,
    R_FAILED,
    R_PENDING,
    R_REJECTED,
    RECREDIT,
    REFUND,
    RK_BOOK,
    TOPUP,
    Book,
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


async def _book_charge(session: AsyncSession, user_id: int, book_id: str) -> WalletEntry | None:
    """The user's paid (price > 0) charge for this book, if any."""
    charge = (await session.execute(
        select(WalletEntry).where(WalletEntry.user_id == user_id, WalletEntry.kind == CHARGE,
                                  WalletEntry.book_id == book_id)
        .order_by(WalletEntry.id))).scalars().first()
    return charge if charge is not None and charge.amount_cents < 0 else None


async def _credited_back(session: AsyncSession, user_id: int, charge_id: int) -> bool:
    """True when this charge was already returned (failed-book re-credit or book refund)."""
    q = select(WalletEntry.id).where(
        WalletEntry.user_id == user_id, WalletEntry.charge_entry_id == charge_id,
        WalletEntry.kind.in_((RECREDIT, BOOK_REFUND)))
    return (await session.execute(q)).first() is not None


async def create_book_request(session: AsyncSession, user: User, book_id: str
                              ) -> RefundRequest:
    """A buyer asks for one finished (or failed) book's price back in the wallet.
    404 unknown book, 400 not finished / never charged / already returned, 409 a request for
    this book exists already (pending, approved or denied: one decision per book)."""
    await lock_user(session, user.id)
    book = await session.get(Book, book_id)
    if book is None or book.user_id != user.id:
        await session.rollback()
        raise BillingError("Book not found.", 404)
    if book.status not in (DONE, FAILED):
        await session.rollback()
        raise BillingError("A refund can only be requested once the book is finished.", 400)
    charge = await _book_charge(session, user.id, book_id)
    if charge is None:
        await session.rollback()
        raise BillingError("This book was free, so there is nothing to refund.", 400)
    if await _credited_back(session, user.id, charge.id):
        await session.rollback()
        raise BillingError("This book was already refunded.", 400)
    existing = (await session.execute(select(RefundRequest).where(
        RefundRequest.user_id == user.id, RefundRequest.kind == RK_BOOK,
        RefundRequest.book_id == book_id).order_by(RefundRequest.id.desc()))).scalars().first()
    if existing is not None and existing.status != R_FAILED:
        word = {R_PENDING: "is already pending", R_APPROVED: "was already approved",
                R_REJECTED: "was already denied"}.get(existing.status, "already exists")
        await session.rollback()
        raise BillingError(f"A refund request for this book {word}.", 409)
    req = RefundRequest(user_id=user.id, topup_entry_id=charge.id, kind=RK_BOOK,
                        book_id=book_id, amount_cents=-charge.amount_cents)
    session.add(req)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise BillingError("A refund request for this book is already pending.", 409) from exc
    log.info("book refund requested id=%s user_id=%s book_id=%s cents=%s", req.id, user.id,
             book_id, req.amount_cents)
    return req


async def book_refund_states(session: AsyncSession, user_id: int, book_ids: list[str]
                             ) -> dict[str, dict]:
    """Per book id: {"status": None | "pending" | "refunded" | "denied", "eligible": bool,
    "amount_cents": int, "request_id": int | None}. Eligible = a refund can be requested now
    (status checks of the book itself are the caller's: only done / failed books)."""
    out = {b: {"status": None, "eligible": False, "amount_cents": 0, "request_id": None}
           for b in book_ids}
    if not book_ids:
        return out
    charges = {}
    for c in (await session.execute(select(WalletEntry).where(
            WalletEntry.user_id == user_id, WalletEntry.kind == CHARGE,
            WalletEntry.book_id.in_(book_ids), WalletEntry.amount_cents < 0)
            .order_by(WalletEntry.id.desc()))).scalars():
        charges[c.book_id] = c
    back = set((await session.execute(select(WalletEntry.charge_entry_id).where(
        WalletEntry.user_id == user_id, WalletEntry.kind.in_((RECREDIT, BOOK_REFUND)),
        WalletEntry.charge_entry_id.in_([c.id for c in charges.values()] or [-1])))
    ).scalars().all())
    reqs: dict[str, RefundRequest] = {}
    for r in (await session.execute(select(RefundRequest).where(
            RefundRequest.user_id == user_id, RefundRequest.kind == RK_BOOK,
            RefundRequest.book_id.in_(book_ids)).order_by(RefundRequest.id))).scalars():
        reqs[r.book_id] = r  # newest wins
    for b in book_ids:
        st, charge, req = out[b], charges.get(b), reqs.get(b)
        if charge is None:
            continue
        st["amount_cents"] = -charge.amount_cents
        if req is not None and req.status == R_PENDING:
            st["status"], st["request_id"] = "pending", req.id
        elif req is not None and req.status == R_APPROVED:
            st["status"], st["request_id"] = "refunded", req.id
        elif req is not None and req.status == R_REJECTED:
            st["status"], st["request_id"] = "denied", req.id
        elif charge.id not in back:
            st["eligible"] = True
            if req is not None:
                st["request_id"] = req.id
    return out


async def _approve_book(session: AsyncSession, req: RefundRequest, admin: User,
                        note: str) -> RefundRequest:
    await lock_user(session, req.user_id)
    await session.refresh(req)
    if req.status != R_PENDING:
        status = req.status
        await session.rollback()
        raise BillingError(f"This request is already {status}.", 409)
    charge = await session.get(WalletEntry, req.topup_entry_id)
    if charge is None or charge.kind != CHARGE or charge.amount_cents >= 0:
        req.status, req.admin_user_id, req.decided_at = R_FAILED, admin.id, utcnow()
        req.admin_note = "No paid charge found for this book."
        await session.commit()
        raise BillingError(req.admin_note, 400)
    if await _credited_back(session, req.user_id, charge.id):
        req.status, req.admin_user_id, req.decided_at = R_FAILED, admin.id, utcnow()
        req.admin_note = "This book was already refunded."
        await session.commit()
        raise BillingError(req.admin_note, 409)
    cents = -charge.amount_cents
    await _append(session, req.user_id, kind=BOOK_REFUND, amount_cents=cents,
                  description=f"Book refund: {charge.description}"[:300],
                  book_id=req.book_id, charge_entry_id=charge.id)
    req.status, req.amount_cents = R_APPROVED, cents
    req.admin_user_id, req.decided_at, req.admin_note = admin.id, utcnow(), note[:500]
    await session.commit()
    log.info("book refund approved id=%s book_id=%s cents=%s admin_id=%s", req.id,
             req.book_id, cents, admin.id)
    return req


async def approve(session: AsyncSession, request_id: int, admin: User,
                  note: str = "") -> RefundRequest:
    req = await _load_pending(session, request_id)
    if req.kind == RK_BOOK:  # wallet credit only: no Stripe involved
        return await _approve_book(session, req, admin, note)
    stripe_client.require_billing()  # fail fast (503) without touching the request
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
