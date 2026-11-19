import hashlib
import hmac
import json
import time

import pytest

from app.billing import stripe_client, wallet
from app.config import get_settings
from app.db.models import ApiCall, Book
from app.db.session import get_sessionmaker
from tests.conftest import client, login

WH_SECRET = "whsec_test_dummy"


@pytest.fixture
def stripe_env(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_dummy")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", WH_SECRET)
    monkeypatch.setenv("ADMIN_EMAILS", "Boss@Example.test, other@example.test")
    get_settings.cache_clear()
    stripe_client._logged.clear()


def paid_session(user_id, sid="cs_test_1", amount=1000, pi="pi_1", paid=True):
    return {"id": sid, "payment_status": "paid" if paid else "unpaid", "amount_total": amount,
            "currency": "usd", "payment_intent": pi, "client_reference_id": str(user_id),
            "metadata": {"user_id": str(user_id), "purpose": "wallet_topup"}}


def mock_retrieve(monkeypatch, sess):
    async def fake(session_id):
        assert session_id == sess["id"]
        return sess
    monkeypatch.setattr(stripe_client, "retrieve_checkout_session", fake)


def signed(payload: dict, secret=WH_SECRET):
    body = json.dumps(payload).encode()
    ts = int(time.time())
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return body, {"stripe-signature": f"t={ts},v1={sig}", "content-type": "application/json"}


async def fund(user_id, cents, sid):
    async with get_sessionmaker()() as s:
        await wallet.credit_topup(s, user_id, checkout_session_id=sid, amount_cents=cents,
                                  payment_intent_id="pi_" + sid)


async def test_ledger_charge_and_idempotency(stripe_env):
    async with client() as c:
        u = await login(c)
    sm = get_sessionmaker()
    async with sm() as s:
        with pytest.raises(wallet.InsufficientFunds) as ei:
            await wallet.charge_document(s, u["id"], book_id="b1", description="x")
        assert (ei.value.balance_cents, ei.value.price_cents) == (0, 99)
    await fund(u["id"], 500, "cs_a")
    async with sm() as s:
        e1 = await wallet.charge_document(s, u["id"], book_id="b1", description="Book")
        e2 = await wallet.charge_document(s, u["id"], book_id="b1", description="Book")
        assert e1.id == e2.id and e1.amount_cents == -99 and e1.balance_after_cents == 401
        assert await wallet.get_balance_cents(s, u["id"]) == 401
        r1 = await wallet.recredit_document(s, u["id"], book_id="b1", charge_entry_id=e1.id,
                                            reason="failed")
        r2 = await wallet.recredit_document(s, u["id"], book_id="b1", charge_entry_id=e1.id,
                                            reason="failed")
        assert r1.id == r2.id and r1.amount_cents == 99
        assert await wallet.get_balance_cents(s, u["id"]) == 500
        # without a book id every call is a new charge
        await wallet.charge_document(s, u["id"], book_id=None, description="a")
        await wallet.charge_document(s, u["id"], book_id=None, description="b")
        assert await wallet.get_balance_cents(s, u["id"]) == 302


async def test_insufficient_funds_response():
    r = wallet.insufficient_funds_response(wallet.InsufficientFunds(10, 99))
    assert r.status_code == 402
    assert json.loads(r.body) == {"error": "Not enough balance. Add funds to make a book.",
                                  "balance_cents": 10, "price_cents": 99}


async def test_wallet_requires_login_and_reports(stripe_env):
    async with client() as c:
        assert (await c.get("/api/wallet")).status_code == 401
        await login(c)
        body = (await c.get("/api/wallet")).json()
        assert body == {"balance_cents": 0, "currency": "usd", "book_price_cents": 99,
                        "default_tier": "cost_efficient",
                        "tiers": [
                            {"id": "professional", "label": "Professional",
                             "price_cents": 399, "avg_cost_per_book_usd": None,
                             "books_costed": 0},
                            {"id": "cost_efficient", "label": "Cost-Efficient",
                             "price_cents": 99, "avg_cost_per_book_usd": None,
                             "books_costed": 0}],
                        "presets_cents": [500, 1000, 2000], "custom_min_cents": 100,
                        "custom_max_cents": 10000, "avg_cost_per_book_usd": None,
                        "books_costed": 0}


@pytest.mark.parametrize("amount", [99, 10001, 0, -5, "500", 5.5, None, True])
async def test_checkout_rejects_bad_amounts(stripe_env, monkeypatch, amount):
    async def boom(*a, **k):
        raise AssertionError("Stripe must not be called")
    monkeypatch.setattr(stripe_client, "create_checkout_session", boom)
    async with client() as c:
        await login(c)
        r = await c.post("/api/wallet/checkout", json={"amount_cents": amount})
        assert r.status_code == 400


@pytest.mark.parametrize("amount", [500, 1000, 2000, 100, 10000, 1234])
async def test_checkout_ok(stripe_env, monkeypatch, amount):
    seen = {}

    async def fake(user_id, cents):
        seen.update(user_id=user_id, cents=cents)
        return {"id": "cs_test_9", "url": "https://checkout.stripe.test/c/pay/cs_test_9"}
    monkeypatch.setattr(stripe_client, "create_checkout_session", fake)
    async with client() as c:
        u = await login(c)
        r = await c.post("/api/wallet/checkout", json={"amount_cents": amount})
        assert r.status_code == 200
        assert r.json() == {"checkout_url": "https://checkout.stripe.test/c/pay/cs_test_9"}
    assert seen == {"user_id": u["id"], "cents": amount}


async def test_checkout_session_params(stripe_env, monkeypatch):
    got = {}

    async def fake_call(fn, **kw):
        got.update(kw)
        return {"id": "cs_x", "url": "u"}
    monkeypatch.setattr(stripe_client, "_call", fake_call)
    await stripe_client.create_checkout_session(7, 1500)
    assert got["mode"] == "payment" and got["client_reference_id"] == "7"
    assert got["metadata"] == {"user_id": "7", "purpose": "wallet_topup"}
    item = got["line_items"][0]
    assert item["price_data"]["unit_amount"] == 1500
    assert item["price_data"]["currency"] == "usd"
    assert item["price_data"]["product_data"]["name"] == "PolyComic wallet top-up"
    assert got["success_url"] == (
        "http://test/wallet?checkout=success&session_id={CHECKOUT_SESSION_ID}")
    assert got["cancel_url"] == "http://test/wallet?checkout=cancel"


async def test_live_key_refused(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_nope")
    get_settings.cache_clear()
    stripe_client._logged.clear()
    assert "test-mode" in stripe_client.billing_status()
    async with client() as c:
        await login(c)
        r = await c.post("/api/wallet/checkout", json={"amount_cents": 500})
        assert r.status_code == 503
        r = await c.post("/api/wallet/checkout/confirm", json={"session_id": "cs_test_1"})
        assert r.status_code == 503


async def test_confirm_credits_once_and_webhook_dedupes(stripe_env, monkeypatch):
    async with client() as c:
        u = await login(c)
        mock_retrieve(monkeypatch, paid_session(u["id"]))
        r = await c.post("/api/wallet/checkout/confirm", json={"session_id": "cs_test_1"})
        assert r.json() == {"credited": True, "balance_cents": 1000}
        r = await c.post("/api/wallet/checkout/confirm", json={"session_id": "cs_test_1"})
        assert r.json() == {"credited": False, "balance_cents": 1000}
        event = {"type": "checkout.session.completed",
                 "data": {"object": paid_session(u["id"])}}
        body, headers = signed(event)
        r = await c.post("/api/stripe/webhook", content=body, headers=headers)
        assert r.status_code == 200 and r.json()["credited"] is False
        assert (await c.get("/api/wallet")).json()["balance_cents"] == 1000
        tx = (await c.get("/api/wallet/transactions")).json()
        assert len(tx) == 1 and tx[0]["kind"] == "topup" and tx[0]["balance_after_cents"] == 1000


async def test_webhook_credits_then_confirm_noop(stripe_env, monkeypatch):
    async with client() as c:
        u = await login(c)
        body, headers = signed({"type": "checkout.session.completed",
                                "data": {"object": paid_session(u["id"], amount=500)}})
        r = await c.post("/api/stripe/webhook", content=body, headers=headers)
        assert r.json()["credited"] is True
        mock_retrieve(monkeypatch, paid_session(u["id"], amount=500))
        r = await c.post("/api/wallet/checkout/confirm", json={"session_id": "cs_test_1"})
        assert r.json() == {"credited": False, "balance_cents": 500}


async def test_webhook_bad_signature_and_ignored_events(stripe_env):
    async with client() as c:
        u = await login(c)
        event = {"type": "checkout.session.completed", "data": {"object": paid_session(u["id"])}}
        body, headers = signed(event, secret="whsec_wrong")
        assert (await c.post("/api/stripe/webhook", content=body, headers=headers)
                ).status_code == 400
        assert (await c.post("/api/stripe/webhook", content=body)).status_code == 400
        body, headers = signed({"type": "charge.succeeded", "data": {"object": {}}})
        r = await c.post("/api/stripe/webhook", content=body, headers=headers)
        assert r.status_code == 200
        body, headers = signed({"type": "checkout.session.completed",
                                "data": {"object": paid_session(u["id"], paid=False)}})
        assert (await c.post("/api/stripe/webhook", content=body, headers=headers)
                ).status_code == 200
        assert (await c.get("/api/wallet")).json()["balance_cents"] == 0


async def test_confirm_rules(stripe_env, monkeypatch):
    async with client() as c:
        u = await login(c)
        assert (await c.post("/api/wallet/checkout/confirm", json={"session_id": "x"})
                ).status_code == 400
        mock_retrieve(monkeypatch, paid_session(u["id"] + 99))  # someone else's
        r = await c.post("/api/wallet/checkout/confirm", json={"session_id": "cs_test_1"})
        assert r.status_code == 403
        mock_retrieve(monkeypatch, paid_session(u["id"], paid=False))
        r = await c.post("/api/wallet/checkout/confirm", json={"session_id": "cs_test_1"})
        assert r.json() == {"credited": False, "balance_cents": 0}


async def test_refund_request_amount_rules(stripe_env):
    async with client() as c:
        u = await login(c)
        await fund(u["id"], 1000, "cs_a")
        async with get_sessionmaker()() as s:
            await wallet.charge_document(s, u["id"], book_id="b1", description="Book")
        tx = (await c.get("/api/wallet/transactions")).json()
        topup_id = next(t["id"] for t in tx if t["kind"] == "topup")
        assert [t["kind"] for t in tx] == ["charge", "topup"]  # newest first
        assert (await c.post("/api/wallet/refund-requests", json={"topup_id": 9999})
                ).status_code == 404
        r = await c.post("/api/wallet/refund-requests", json={"topup_id": topup_id})
        assert r.status_code == 201
        assert r.json()["status"] == "pending" and r.json()["amount_cents"] == 901  # balance
        assert (await c.post("/api/wallet/refund-requests", json={"topup_id": topup_id})
                ).status_code == 409
        tx = (await c.get("/api/wallet/transactions")).json()
        assert next(t for t in tx if t["id"] == topup_id)["status"] == "refund pending"
        assert len((await c.get("/api/wallet/refund-requests")).json()) == 1


async def test_refund_request_zero_balance(stripe_env):
    async with client() as c:
        u = await login(c)
        await fund(u["id"], 99, "cs_a")
        async with get_sessionmaker()() as s:
            await wallet.charge_document(s, u["id"], book_id="b1", description="Book")
        tx = (await c.get("/api/wallet/transactions")).json()
        topup_id = next(t["id"] for t in tx if t["kind"] == "topup")
        r = await c.post("/api/wallet/refund-requests", json={"topup_id": topup_id})
        assert r.status_code == 400 and "error" in r.json()


async def test_is_admin_in_me(stripe_env):
    async with client() as c:
        await login(c, email="someone@example.test")
        assert (await c.get("/api/me")).json()["user"]["is_admin"] is False
        await login(c, email="BOSS@example.test")
        assert (await c.get("/api/me")).json()["user"]["is_admin"] is True


async def test_avg_cost_in_wallet(stripe_env):
    async with client() as c:
        u = await login(c)
        async with get_sessionmaker()() as s:
            for i, cost in enumerate((0.20, 0.40)):
                s.add(Book(id=f"b{i}", user_id=u["id"], story="jack", title="t", preset="p",
                           detail=1, hero_name="h", pronouns="she", status="done"))
                await s.flush()
                s.add(ApiCall(book_id=f"b{i}", user_id=u["id"], provider="fake", model="m",
                              kind="page", page=1, est_cost_usd=cost))
            s.add(ApiCall(book_id=None, user_id=None, provider="fake", model="m",
                          kind="plate", est_cost_usd=5.0))
            await s.commit()
        body = (await c.get("/api/wallet")).json()
        assert body["avg_cost_per_book_usd"] == 0.3 and body["books_costed"] == 2


async def test_pages_and_robots(stripe_env):
    async with client() as c:
        assert (await c.get("/wallet")).status_code == 200
        r = await c.get("/hw1")
        assert r.status_code == 200 and "noindex" in r.text
        robots = (await c.get("/robots.txt")).text
        assert "/hw1" not in robots and "Disallow: /api/" in robots


async def _pending_request(c, admin_c, user_id):
    await fund(user_id, 1000, "cs_r")
    tx = (await c.get("/api/wallet/transactions")).json()
    topup_id = next(t["id"] for t in tx if t["kind"] == "topup")
    return (await c.post("/api/wallet/refund-requests", json={"topup_id": topup_id})
            ).json()["id"]


async def test_approve_transient_error_stays_pending_then_succeeds(stripe_env, monkeypatch):
    from app.billing.stripe_client import BillingError

    attempts = []

    async def flaky(pi, cents, req_id):
        attempts.append(req_id)
        if len(attempts) == 1:
            raise BillingError("Could not reach the payment service. Please try again.", 502,
                               transient=True)
        return {"id": "re_ok"}
    monkeypatch.setattr(stripe_client, "create_refund", flaky)
    async with client() as c, client() as admin_c:
        u = await login(c)
        await login(admin_c, email="boss@example.test")
        rid = await _pending_request(c, admin_c, u["id"])
        r = await admin_c.post(f"/api/admin/refund-requests/{rid}/approve")
        assert r.status_code == 502
        reqs = (await admin_c.get("/api/admin/refund-requests?status=all")).json()
        assert reqs[0]["status"] == "pending" and reqs[0]["admin_note"] == ""
        assert (await c.get("/api/wallet")).json()["balance_cents"] == 1000
        r = await admin_c.post(f"/api/admin/refund-requests/{rid}/approve")
        assert r.status_code == 200 and r.json()["status"] == "approved"
        tx = (await c.get("/api/wallet/transactions")).json()
        assert [t["kind"] for t in tx].count("refund") == 1
        assert (await c.get("/api/wallet")).json()["balance_cents"] == 0


async def test_approve_billing_not_configured_fails_fast(stripe_env, monkeypatch):
    async with client() as c, client() as admin_c:
        u = await login(c)
        await login(admin_c, email="boss@example.test")
        rid = await _pending_request(c, admin_c, u["id"])
        monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_nope")
        get_settings.cache_clear()
        r = await admin_c.post(f"/api/admin/refund-requests/{rid}/approve")
        assert r.status_code == 503
        monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_dummy")
        get_settings.cache_clear()
        reqs = (await admin_c.get("/api/admin/refund-requests")).json()
        assert reqs[0]["status"] == "pending"


async def test_approve_invalid_request_marks_failed(stripe_env, monkeypatch):
    import stripe

    def reject(**kw):
        raise stripe.InvalidRequestError("charge already refunded", param=None)
    monkeypatch.setattr(stripe.Refund, "create", reject)
    async with client() as c, client() as admin_c:
        u = await login(c)
        await login(admin_c, email="boss@example.test")
        rid = await _pending_request(c, admin_c, u["id"])
        r = await admin_c.post(f"/api/admin/refund-requests/{rid}/approve")
        assert r.status_code == 502
        reqs = (await admin_c.get("/api/admin/refund-requests?status=all")).json()
        assert reqs[0]["status"] == "failed" and "rejected" in reqs[0]["admin_note"]
