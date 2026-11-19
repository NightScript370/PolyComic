import re

import httpx

from app.main import create_app
from app.web import assets


def _client():
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://test")


async def test_rendered_pages_version_every_static_ref():
    async with _client() as c:
        for path in ("/", "/hw1", "/wallet", "/admin"):
            r = await c.get(path)
            refs = re.findall(r'(?:src|href)="(/static/[^"]+)"', r.text)
            assert refs or path in ("/hw1", "/wallet", "/admin")
            for ref in refs:
                assert re.search(r"\?v=[0-9a-f]{10}$", ref), (path, ref)
        assert "?v=" in (await c.get("/")).text


def test_hash_changes_with_content(tmp_path, monkeypatch):
    monkeypatch.setattr(assets, "STATIC_DIR", tmp_path)
    f = tmp_path / "x.js"
    f.write_text("a")
    u1 = assets.static_url("x.js")
    assert re.fullmatch(r"/static/x\.js\?v=[0-9a-f]{10}", u1)
    assert assets.static_url("x.js") == u1
    f.write_text("bb")
    import os
    st = f.stat()
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    assert assets.static_url("x.js") != u1


async def test_static_cache_control():
    async with _client() as c:
        plain = await c.get("/static/js/common.js")
        versioned = await c.get(assets.static_url("js/common.js"))
    assert plain.headers["cache-control"] == "no-cache"
    assert versioned.headers["cache-control"] == "public, max-age=31536000, immutable"
