"""Cache-busted static URLs and cache headers for /static."""

from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi.staticfiles import StaticFiles
from jinja2 import Environment
from starlette.responses import Response
from starlette.types import Scope

STATIC_DIR = Path(__file__).parent / "static"

# path -> (mtime_ns, hash)
_hash_cache: dict[Path, tuple[int, str]] = {}


def file_hash(path: Path) -> str:
    """First 10 hex chars of the sha1 of the file; re-hashed when its mtime changes."""
    mtime = path.stat().st_mtime_ns
    cached = _hash_cache.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    digest = hashlib.sha1(path.read_bytes()).hexdigest()[:10]
    _hash_cache[path] = (mtime, digest)
    return digest


def static_url(path: str) -> str:
    rel = path.lstrip("/")
    try:
        return f"/static/{rel}?v={file_hash(STATIC_DIR / rel)}"
    except OSError:
        return f"/static/{rel}"


def register_static_url(env: Environment) -> None:
    env.globals["static_url"] = static_url


class CachedStaticFiles(StaticFiles):
    """no-cache for plain URLs; immutable for versioned (?v=) URLs."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            query = scope.get("query_string", b"")
            versioned = any(p.startswith(b"v=") and len(p) > 2 for p in query.split(b"&"))
            response.headers["Cache-Control"] = (
                "public, max-age=31536000, immutable" if versioned else "no-cache"
            )
        return response
