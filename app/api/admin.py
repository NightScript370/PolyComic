"""Admin API (ADMIN_EMAILS): refund requests and API cost statistics. 404 for everyone else."""

from __future__ import annotations

import statistics
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.api.billing import billing_error, json_body, refund_json, rows_by_tier
from app.api.common import ApiError
from app.auth.deps import DbSession, OptionalUser
from app.billing import refunds
from app.billing.stripe_client import BillingError
from app.billing.wallet import get_balance_cents
from app.db import repo
from app.db.models import User, WalletEntry
from app.tiers import Tier, tier_config

router = APIRouter(prefix="/api/admin")


async def admin_user(user: OptionalUser) -> User:
    if not refunds.is_admin(user):
        raise ApiError(404, "Not Found")
    return user


AdminUser = Annotated[User, Depends(admin_user)]


async def _request_json(session, r) -> dict:
    u = await session.get(User, r.user_id)
    topup = await session.get(WalletEntry, r.topup_entry_id)
    return {**refund_json(r), "user_id": r.user_id,
            "user_email": u.email if u else None, "user_name": u.name if u else None,
            "topup_amount_cents": topup.amount_cents if topup else None,
            "balance_cents": await get_balance_cents(session, r.user_id)}


@router.get("/refund-requests")
async def list_requests(admin: AdminUser, session: DbSession, status: str = "pending"):
    rows = await repo.list_refund_requests(
        session, status=None if status == "all" else "pending")
    return JSONResponse([await _request_json(session, r) for r in rows])


@router.post("/refund-requests/{request_id}/approve")
async def approve(request_id: int, request: Request, admin: AdminUser, session: DbSession):
    note = await _note(request)
    try:
        req = await refunds.approve(session, request_id, admin, note)
    except BillingError as exc:
        return billing_error(exc)
    return JSONResponse(await _request_json(session, req))


@router.post("/refund-requests/{request_id}/reject")
async def reject(request_id: int, request: Request, admin: AdminUser, session: DbSession):
    note = await _note(request)
    try:
        req = await refunds.reject(session, request_id, admin, note)
    except BillingError as exc:
        return billing_error(exc)
    return JSONResponse(await _request_json(session, req))


async def _note(request: Request) -> str:
    if not (await request.body()):
        return ""
    return str((await json_body(request)).get("note") or "")


def _stats(per_book: list[float]) -> tuple[float | None, float | None]:
    """(average, median) cost per book, rounded; (None, None) without books."""
    if not per_book:
        return None, None
    return (round(sum(per_book) / len(per_book), 4),
            round(statistics.median(per_book), 4))


def _tier_stats(rows: list[repo.BookCostRow]) -> list[dict]:
    """Per tier: price, books done, average / median API cost and margin per book."""
    out = []
    for tier_id, tier_rows in rows_by_tier(rows).items():
        try:
            cfg = tier_config(tier_id)
        except ValueError:  # an unknown tier value in the DB: show it, without a price
            label, price = tier_id, None
        else:
            label, price = cfg.label, cfg.price_cents
        avg, median = _stats([r.cost_usd for r in tier_rows])
        price_usd = None if price is None else round(price / 100, 2)
        out.append({
            "id": tier_id, "label": label, "price_cents": price, "price_usd": price_usd,
            "books_done": len(tier_rows), "avg_cost_per_book_usd": avg,
            "median_cost_per_book_usd": median,
            "margin_per_book_usd": (None if avg is None or price_usd is None
                                    else round(price_usd - avg, 4)),
        })
    return out


@router.get("/costs")
async def costs(admin: AdminUser, session: DbSession) -> JSONResponse:
    rows = await repo.done_book_costs(session)
    by_kind, failed = await repo.cost_by_kind(session)
    avg, median = _stats([r.cost_usd for r in rows])
    return JSONResponse({
        # Backward compatibility: the Cost-Efficient (default tier) price; see ``tiers``.
        "price_usd": round(tier_config(Tier.COST_EFFICIENT).price_cents / 100, 2),
        "books_done": len(rows),
        "avg_cost_per_book_usd": avg,
        "median_cost_per_book_usd": median,
        "tiers": _tier_stats(rows),
        "total_api_cost_usd": round(sum(c for _, c in by_kind.values()), 4),
        "by_kind": {k: {"calls": n, "cost_usd": round(c, 4)} for k, (n, c) in by_kind.items()},
        "failed_calls": failed,
        "recent_books": [
            {"book_id": r.book_id, "story": r.story, "tier": r.tier,
             "created_at": repo.iso(r.created_at), "cost_usd": round(r.cost_usd, 4),
             "calls": r.calls, "retries": r.retries}
            for r in rows[:20]],
    })
