"""Seeded random streams that are stable across processes and machines."""

import hashlib
import random


def _sub_seed(seed: int, name: str, salt: str | None = None) -> int:
    """Derive a stable sub-seed. Never use `hash()`: it is salted per process.

    Without a salt the input is `"{seed}:{name}"`, as it always was, so unsalted worlds and golden
    scores do not change. A salt goes between them: `"{seed}:{salt}:{name}"`.
    """
    key = f"{seed}:{name}" if salt is None else f"{seed}:{salt}:{name}"
    digest = hashlib.sha256(key.encode()).digest()
    return int.from_bytes(digest[:8], "big")


class RngStreams:
    """Separate streams: `exogenous` (world events, drawn in reset) and `actions`.

    An exported task gets a secret `salt`, so its world cannot be rebuilt from the public seed.
    In-process runs pass no salt.
    """

    def __init__(self, seed: int, salt: str | None = None) -> None:
        self.seed = seed
        self.salt = salt
        self._streams: dict[str, random.Random] = {}
        self.exogenous = self.stream("exogenous")
        self.actions = self.stream("actions")

    def stream(self, name: str) -> random.Random:
        """Get a named stream, created on first use."""
        if name not in self._streams:
            self._streams[name] = random.Random(_sub_seed(self.seed, name, self.salt))
        return self._streams[name]
