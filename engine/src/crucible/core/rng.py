"""Seeded random streams that are stable across processes and machines."""

import hashlib
import random


def _sub_seed(seed: int, name: str) -> int:
    """Derive a stable sub-seed. Never use `hash()`: it is salted per process."""
    digest = hashlib.sha256(f"{seed}:{name}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


class RngStreams:
    """Separate streams: `exogenous` (world events, drawn in reset) and `actions`."""

    def __init__(self, seed: int) -> None:
        self.seed = seed
        self._streams: dict[str, random.Random] = {}
        self.exogenous = self.stream("exogenous")
        self.actions = self.stream("actions")

    def stream(self, name: str) -> random.Random:
        """Get a named stream, created on first use."""
        if name not in self._streams:
            self._streams[name] = random.Random(_sub_seed(self.seed, name))
        return self._streams[name]
