"""Wallet helpers for pipeline/API tests (books and Quick Scenes are charged)."""

from __future__ import annotations

import itertools

from app.billing import wallet
from app.db.models import WalletEntry
from app.db.session import get_sessionmaker
from tests.conftest import login

_ids = itertools.count(1)


async def fund(user_id: int, cents: int = 10_000) -> None:
    async with get_sessionmaker()() as s:
        await wallet.credit_topup(s, user_id, checkout_session_id=f"cs_test_fund_{next(_ids)}",
                                  amount_cents=cents, payment_intent_id=None)


async def login_funded(c, email: str = "kid-parent@example.test", cents: int = 10_000,
                       **kw) -> dict:
    user = await login(c, email=email, **kw)
    await fund(user["id"], cents)
    return user


async def balance(user_id: int) -> int:
    async with get_sessionmaker()() as s:
        return await wallet.get_balance_cents(s, user_id)


async def entries(user_id: int) -> list[WalletEntry]:
    from sqlalchemy import select

    async with get_sessionmaker()() as s:
        q = select(WalletEntry).where(WalletEntry.user_id == user_id).order_by(WalletEntry.id)
        return list((await s.execute(q)).scalars().all())
