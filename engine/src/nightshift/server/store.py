"""Session store: live episodes by id, with a time-to-live."""

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from nightshift.core.session import EpisodeSession


@dataclass
class Entry:
    """One live episode. The lock allows one call at a time."""

    session: EpisodeSession
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class SessionStore(Protocol):
    def get(self, episode_id: str) -> Entry | None: ...

    def put(self, episode_id: str, entry: Entry) -> None: ...

    def delete(self, episode_id: str) -> None: ...


class InMemorySessionStore:
    """Dict-backed store. An entry unused for `ttl_seconds` is dropped on the next access."""

    def __init__(self, ttl_seconds: int, clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl = ttl_seconds
        self._clock = clock
        self._items: dict[str, tuple[Entry, float]] = {}

    def get(self, episode_id: str) -> Entry | None:
        item = self._items.get(episode_id)
        if item is None:
            return None
        entry, last_used = item
        now = self._clock()
        if now - last_used >= self._ttl:
            del self._items[episode_id]
            return None
        self._items[episode_id] = (entry, now)
        return entry

    def put(self, episode_id: str, entry: Entry) -> None:
        self._items[episode_id] = (entry, self._clock())

    def delete(self, episode_id: str) -> None:
        self._items.pop(episode_id, None)


__all__ = ["Entry", "InMemorySessionStore", "SessionStore"]
