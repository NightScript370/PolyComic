"""In-process pub/sub for book progress, keyed by book id.

Single-process only (the deploy runs one uvicorn worker). Subscribers that join late
get a snapshot rebuilt from the database first (see ``app.jobs.progress.snapshot``),
so nothing here needs to be replayed or persisted.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager

Event = tuple[str, dict]


class EventBroker:
    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue[Event]]] = defaultdict(set)

    def publish(self, book_id: str, event: str, data: dict) -> None:
        for q in list(self._subs.get(book_id, ())):
            q.put_nowait((event, data))

    @contextmanager
    def subscribe(self, book_id: str) -> Iterator[asyncio.Queue[Event]]:
        q: asyncio.Queue[Event] = asyncio.Queue()
        self._subs[book_id].add(q)
        try:
            yield q
        finally:
            subs = self._subs.get(book_id)
            if subs is not None:
                subs.discard(q)
                if not subs:
                    del self._subs[book_id]

    def subscriber_count(self, book_id: str) -> int:
        return len(self._subs.get(book_id, ()))


broker = EventBroker()
