from contextlib import asynccontextmanager

import pytest

from app.billing import stripe_client, wallet
from app.billing.stripe_client import BillingError
from app.config import get_settings
from app.db.models import ApiCall, Book
from app.db.session import get_sessionmaker
from tests.conftest import client, login

ADMIN = "boss@example.test"


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_dummy")
    monkeypatch.setenv("ADMIN_EMAILS", "Boss@Example.test")
    get_settings.cache_clear()


@asynccontextmanager
async def two_clients():
    async with client() as user_c, client() as admin_c:
        user = await login(user_c, email="kid-parent@example.test")
        await login(admin_c, email=ADMIN)
        yield user_c, admin_c, user


async def setup_topup(user_id, cents=1000):
    async with get_sessionmaker()() as s:
        e, _ = await wallet.credit_topup(s, user_id, checkout_session_id="cs_t",
                                         amount_cents=cents, payment_intent_id="pi_t")
        return e.id


async def test_non_admin_gets_404():
    async with client() as anon:
        assert (await anon.get("/api/admin/costs")).status_code == 404
        assert (await anon.get("/admin")).status_code == 404
    async with two_clients() as (user_c, admin_c, _):
        for path in ("/api/admin/costs", "/api/admin/refund-requests"):
            assert (await user_c.get(path)).status_code == 404
            assert (await admin_c.get(path)).status_code == 200
        assert (await user_c.post("/api/admin/refund-requests/1/approve")).status_code == 404
        assert (await user_c.get("/admin")).status_code == 404
        assert (await admin_c.get("/admin")).status_code == 200


async def test_approve_writes_ledger_and_caps(monkeypatch):
    calls = []

    async def fake_refund(pi, cents, req_id):
        calls.append((pi, cents, req_id))
        return {"id": "re_1"}
    monkeypatch.setattr(stripe_client, "create_refund", fake_refund)
    async with two_clients() as (user_c, admin_c, user):
        topup = await setup_topup(user["id"])
        r = await user_c.post("/api/wallet/refund-requests", json={"topup_id": topup})
        assert r.json()["amount_cents"] == 1000
        # user spends money after requesting: approval caps at the balance
        async with get_sessionmaker()() as s:
            await wallet.charge_document(s, user["id"], book_id="b1", description="Book")
        listing = (await admin_c.get("/api/admin/refund-requests")).json()
        assert listing[0]["user_email"] == "kid-parent@example.test"
        assert listing[0]["topup_amount_cents"] == 1000 and listing[0]["balance_cents"] == 901
        rid = r.json()["id"]
        a = await admin_c.post(f"/api/admin/refund-requests/{rid}/approve")
        assert a.status_code == 200 and a.json()["status"] == "approved"
        assert calls == [("pi_t", 901, rid)]
        assert (await user_c.get("/api/wallet")).json()["balance_cents"] == 0
        tx = (await user_c.get("/api/wallet/transactions")).json()
        assert tx[0]["kind"] == "refund" and tx[0]["amount_cents"] == -901
        assert tx[0]["balance_after_cents"] == 0
        assert next(t for t in tx if t["kind"] == "topup")["status"] == "partially refunded"
        # approving again is refused
        assert (await admin_c.post(f"/api/admin/refund-requests/{rid}/approve")
                ).status_code == 409
        assert len(calls) == 1


async def test_approve_stripe_failure(monkeypatch):
    async def fail(pi, cents, req_id):
        raise BillingError("The payment service failed. Please try again.", 502)
    monkeypatch.setattr(stripe_client, "create_refund", fail)
    async with two_clients() as (user_c, admin_c, user):
        topup = await setup_topup(user["id"])
        rid = (await user_c.post("/api/wallet/refund-requests", json={"topup_id": topup})
               ).json()["id"]
        a = await admin_c.post(f"/api/admin/refund-requests/{rid}/approve")
        assert a.status_code == 502
        reqs = (await admin_c.get("/api/admin/refund-requests?status=all")).json()
        assert reqs[0]["status"] == "failed" and "failed" in reqs[0]["admin_note"]
        assert (await user_c.get("/api/wallet")).json()["balance_cents"] == 1000
        assert len((await user_c.get("/api/wallet/transactions")).json()) == 1
        # a failed request frees the top-up for a new request
        assert (await user_c.post("/api/wallet/refund-requests", json={"topup_id": topup})
                ).status_code == 201


async def test_reject():
    async with two_clients() as (user_c, admin_c, user):
        topup = await setup_topup(user["id"])
        rid = (await user_c.post("/api/wallet/refund-requests", json={"topup_id": topup})
               ).json()["id"]
        r = await admin_c.post(f"/api/admin/refund-requests/{rid}/reject",
                               json={"note": "Please contact us"})
        assert r.json()["status"] == "rejected" and r.json()["admin_note"] == "Please contact us"
        assert (await admin_c.get("/api/admin/refund-requests")).json() == []
        assert len((await admin_c.get("/api/admin/refund-requests?status=all")).json()) == 1
        assert (await user_c.get("/api/wallet")).json()["balance_cents"] == 1000


async def test_costs_aggregation():
    async with two_clients() as (user_c, admin_c, user):
        async with get_sessionmaker()() as s:
            for bid, status in (("b1", "done"), ("b2", "done"), ("b3", "failed")):
                s.add(Book(id=bid, user_id=user["id"], story="jack", title="t", preset="p",
                           detail=1, hero_name="h", pronouns="she", status=status))
            await s.flush()

            def call(book, kind, cost, page=None, ok=True):
                s.add(ApiCall(book_id=book, user_id=user["id"], provider="fake", model="m",
                              kind=kind, page=page, est_cost_usd=cost, ok=ok))
            call("b1", "hero", 0.10)
            call("b1", "page", 0.10, 1)
            call("b1", "page", 0.10, 1, ok=False)  # retry
            call("b1", "grade", 0.01, 1)
            call("b2", "hero", 0.20)
            call("b2", "page", 0.20, 1)
            call("b3", "page", 9.0, 1)
            call(None, "plate", 1.0)
            await s.commit()
        c = (await admin_c.get("/api/admin/costs")).json()
        assert c["price_usd"] == 0.99 and c["books_done"] == 2
        assert c["avg_cost_per_book_usd"] == pytest.approx(0.355)
        assert c["median_cost_per_book_usd"] == pytest.approx(0.355)
        assert c["total_api_cost_usd"] == pytest.approx(10.71)
        assert c["failed_calls"] == 1
        assert c["by_kind"]["page"] == {"calls": 4, "cost_usd": pytest.approx(9.4)}
        by_id = {b["book_id"]: b for b in c["recent_books"]}
        assert set(by_id) == {"b1", "b2"}
        assert by_id["b1"]["calls"] == 4 and by_id["b1"]["retries"] == 1
        assert by_id["b2"]["retries"] == 0 and by_id["b1"]["cost_usd"] == pytest.approx(0.31)
