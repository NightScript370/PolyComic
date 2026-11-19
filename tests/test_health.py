import httpx

from app.main import create_app


async def test_healthz():
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert "x-request-id" in r.headers


async def test_index():
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/")
    assert r.status_code == 200
    assert "PolyComic" in r.text


def test_json_log_has_user_and_book_ids():
    import json
    import logging

    from app.logging_setup import JsonFormatter, book_id_var, user_id_var

    rec = logging.LogRecord("app.jobs", logging.INFO, __file__, 1, "stage=%s", ("pages",), None)
    assert "user_id" not in json.loads(JsonFormatter().format(rec))
    t1, t2 = user_id_var.set(7), book_id_var.set("abc")
    try:
        data = json.loads(JsonFormatter().format(rec))
    finally:
        user_id_var.reset(t1)
        book_id_var.reset(t2)
    assert data["user_id"] == 7 and data["book_id"] == "abc" and data["msg"] == "stage=pages"
