"""Wallet API: balance, Stripe Checkout top-ups, transaction history, refund requests."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.api.common import ApiError, error
from app.auth.deps import CurrentUser, DbSession
from app.billing import refunds, stripe_client
from app.billing.stripe_client import BillingError
from app.billing.wallet import (
    CUSTOM_MAX_CENTS,
    CUSTOM_MIN_CENTS,
    PRESETS_CENTS,
    credit_topup,
    get_balance_cents,
    refunded_cents,
)
from app.db import repo
from app.db.models import R_PENDING, TOPUP, RefundRequest
from app.tiers import DEFAULT_TIER, TIERS, Tier, tier_config

log = logging.getLogger("app.billing")
router = APIRouter()


async def json_body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        raise ApiError(400, "Expected a JSON object.")
    return body


def billing_error(exc: BillingError) -> JSONResponse:
    return error(exc.status, exc.message)


def avg_cost(rows: list[repo.BookCostRow]) -> tuple[float | None, int]:
    if not rows:
        return None, 0
    return round(sum(r.cost_usd for r in rows) / len(rows), 4), len(rows)


def rows_by_tier(rows: list[repo.BookCostRow]) -> dict[str, list[repo.BookCostRow]]:
    """Done-book cost rows per tier id (every known tier present, possibly empty)."""
    out: dict[str, list[repo.BookCostRow]] = {t.value: [] for t in TIERS}
    for r in rows:
        out.setdefault(r.tier, []).append(r)
    return out


def tiers_json(rows: list[repo.BookCostRow]) -> list[dict]:
    """Per-tier price and average API cost per done book (for /api/wallet)."""
    grouped = rows_by_tier(rows)
    out = []
    for t in TIERS:
        cfg = tier_config(t)
        avg, n = avg_cost(grouped[t.value])
        out.append({"id": t.value, "label": cfg.label, "price_cents": cfg.price_cents,
                    "avg_cost_per_book_usd": avg, "books_costed": n})
    return out


def refund_json(r: RefundRequest) -> dict:
    return {"id": r.id, "topup_id": r.topup_entry_id, "status": r.status,
            "amount_cents": r.amount_cents, "admin_note": r.admin_note,
            "created_at": repo.iso(r.created_at), "decided_at": repo.iso(r.decided_at)}


@router.get("/api/wallet")
async def wallet(user: CurrentUser, session: DbSession) -> JSONResponse:
    rows = await repo.done_book_costs(session)
    avg, n = avg_cost(rows)
    return JSONResponse({
        "balance_cents": await get_balance_cents(session, user.id),
        "currency": "usd",
        # Backward compatibility: the Cost-Efficient price (the default tier).
        "book_price_cents": tier_config(Tier.COST_EFFICIENT).price_cents,
        "default_tier": DEFAULT_TIER.value,
        "tiers": tiers_json(rows),
        "presets_cents": list(PRESETS_CENTS),
        "custom_min_cents": CUSTOM_MIN_CENTS,
        "custom_max_cents": CUSTOM_MAX_CENTS,
        "avg_cost_per_book_usd": avg,
        "books_costed": n,
    }, headers={"Cache-Control": "private, no-store"})


@router.post("/api/wallet/checkout")
async def checkout(request: Request, user: CurrentUser) -> JSONResponse:
    body = await json_body(request)
    amount = body.get("amount_cents")
    if (not isinstance(amount, int) or isinstance(amount, bool)
            or not CUSTOM_MIN_CENTS <= amount <= CUSTOM_MAX_CENTS):
        return error(400, f"Choose an amount between ${CUSTOM_MIN_CENTS // 100} and "
                          f"${CUSTOM_MAX_CENTS // 100}.")
    try:
        sess = await stripe_client.create_checkout_session(user.id, amount)
    except BillingError as exc:
        return billing_error(exc)
    if not sess.get("url"):
        return error(502, "The payment service failed. Please try again.")
    return JSONResponse({"checkout_url": sess["url"]})


def _paid_session_fields(sess: dict) -> tuple[int, int, str, str | None] | None:
    """(user_id, amount_cents, session_id, payment_intent) for a paid wallet top-up session,
    else None."""
    meta = sess.get("metadata") or {}
    if sess.get("payment_status") != "paid" or meta.get("purpose") != "wallet_topup":
        return None
    if (sess.get("currency") or "usd").lower() != "usd":
        return None
    try:
        uid = int(meta.get("user_id") or sess.get("client_reference_id"))
        amount = int(sess["amount_total"])
    except (TypeError, ValueError, KeyError):
        return None
    pi = sess.get("payment_intent")
    if isinstance(pi, dict):
        pi = pi.get("id")
    return uid, amount, str(sess["id"]), pi


@router.post("/api/wallet/checkout/confirm")
async def checkout_confirm(request: Request, user: CurrentUser, session: DbSession
                           ) -> JSONResponse:
    body = await json_body(request)
    session_id = body.get("session_id")
    if not isinstance(session_id, str) or not session_id.startswith("cs_"):
        return error(400, "session_id is required.")
    try:
        sess = await stripe_client.retrieve_checkout_session(session_id)
    except BillingError as exc:
        return billing_error(exc)
    meta = sess.get("metadata") or {}
    owner = meta.get("user_id") or sess.get("client_reference_id")
    if str(owner) != str(user.id):
        return error(403, "That payment belongs to a different account.")
    fields = _paid_session_fields(sess)
    if fields is None:
        return JSONResponse({"credited": False,
                             "balance_cents": await get_balance_cents(session, user.id)})
    uid, amount, sid, pi = fields
    _, created = await credit_topup(session, uid, checkout_session_id=sid,
                                    amount_cents=amount, payment_intent_id=pi)
    return JSONResponse({"credited": created,
                         "balance_cents": await get_balance_cents(session, user.id)})


@router.post("/api/stripe/webhook")
async def stripe_webhook(request: Request, session: DbSession) -> JSONResponse:
    payload = await request.body()
    try:
        event = stripe_client.construct_event(payload, request.headers.get("stripe-signature"))
    except BillingError as exc:
        return billing_error(exc)
    if event.get("type") != "checkout.session.completed":
        return JSONResponse({"received": True, "ignored": True})
    obj = (event.get("data") or {}).get("object") or {}
    fields = _paid_session_fields(obj)
    if fields is None:
        log.info("webhook: checkout session not a paid wallet top-up id=%s", obj.get("id"))
        return JSONResponse({"received": True, "ignored": True})
    uid, amount, sid, pi = fields
    if await repo.get_user(session, uid) is None:
        log.warning("webhook: unknown user for session id=%s", sid)
        return JSONResponse({"received": True, "ignored": True})
    _, created = await credit_topup(session, uid, checkout_session_id=sid,
                                    amount_cents=amount, payment_intent_id=pi)
    return JSONResponse({"received": True, "credited": created})


@router.get("/api/wallet/transactions")
async def transactions(user: CurrentUser, session: DbSession) -> JSONResponse:
    entries = await repo.list_wallet_entries(session, user.id)
    requests = await repo.list_refund_requests(session, user_id=user.id)
    pending = {r.topup_entry_id for r in requests if r.status == R_PENDING}
    out = []
    for e in entries:
        status = None
        if e.kind == TOPUP:
            if e.id in pending:
                status = "refund pending"
            else:
                done = await refunded_cents(session, e.id)
                if done >= e.amount_cents:
                    status = "refunded"
                elif done > 0:
                    status = "partially refunded"
        out.append({"id": e.id, "created_at": repo.iso(e.created_at), "kind": e.kind,
                    "amount_cents": e.amount_cents,
                    "balance_after_cents": e.balance_after_cents,
                    "description": e.description, "book_id": e.book_id, "status": status})
    return JSONResponse(out, headers={"Cache-Control": "private, no-store"})


@router.post("/api/wallet/refund-requests", status_code=201)
async def create_refund_request(request: Request, user: CurrentUser, session: DbSession
                                ) -> JSONResponse:
    body = await json_body(request)
    topup_id = body.get("topup_id")
    if not isinstance(topup_id, int) or isinstance(topup_id, bool):
        return error(400, "topup_id is required.")
    try:
        req = await refunds.create_request(session, user, topup_id)
    except BillingError as exc:
        return billing_error(exc)
    return JSONResponse({"id": req.id, "status": req.status, "amount_cents": req.amount_cents},
                        status_code=201)


@router.get("/api/wallet/refund-requests")
async def list_refund_requests(user: CurrentUser, session: DbSession) -> JSONResponse:
    rows = await repo.list_refund_requests(session, user_id=user.id)
    return JSONResponse([refund_json(r) for r in rows],
                        headers={"Cache-Control": "private, no-store"})
