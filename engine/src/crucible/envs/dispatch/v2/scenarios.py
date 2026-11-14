"""Private action-independent v2 workload distributions.

Each named random stream is fixed before any action. Regimes are experiments,
not an assertion that a particular frontier-performance gap has been achieved.
"""

from dataclasses import dataclass

from crucible.core.rng import RngStreams
from crucible.envs.dispatch.world import Disruption, Order, Road, World


@dataclass(frozen=True)
class Regime:
    name: str
    order_range: tuple[int, int]
    capacity_range: tuple[int, int]
    size_range: tuple[int, int]
    initial_orders: int
    burst_fraction: float
    urgent_fraction: float
    high_value_late: bool = False


REGIMES: dict[str, Regime] = {
    "A": Regime("A", (40, 44), (7, 8), (1, 3), 9, 0.20, 0.25),
    "B": Regime("B", (48, 52), (6, 8), (2, 3), 12, 0.20, 0.25),
    "C": Regime("C", (48, 52), (6, 8), (2, 3), 12, 0.65, 0.45),
    "D": Regime("D", (50, 55), (6, 8), (2, 3), 12, 0.65, 0.45, True),
}


def make_world(seed: int, salt: str | None = None, *, regime: str = "A") -> World:
    """Generate the entire v2 world without observing policy behavior."""
    config = REGIMES[regime]
    streams = RngStreams(seed, salt)
    network = streams.stream("dispatch@2:network")
    demand = streams.stream(f"dispatch@2:{regime}:demand")
    traffic = streams.stream("dispatch@2:traffic")
    fleet = streams.stream(f"dispatch@2:{regime}:fleet")
    roads = [
        Road(f"n{y * 5 + x}", f"n{(y + dy) * 5 + x + dx}", 2.0, network.randint(6, 10))
        for y in range(5)
        for x in range(5)
        for dx, dy in ((1, 0), (0, 1))
        if x + dx < 5 and y + dy < 5
    ]
    for y in range(4):
        for x in range(4):
            if network.random() < 0.20:
                roads.append(
                    Road(
                        f"n{y * 5 + x}",
                        f"n{(y + 1) * 5 + x + 1}",
                        2.828427,
                        network.randint(8, 12),
                    )
                )
    # Floyd-Warshall is private generation-time feasibility checking, not a policy oracle.
    times = [[0.0 if i == j else float("inf") for j in range(25)] for i in range(25)]
    for road in roads:
        a, b = int(road.a[1:]), int(road.b[1:])
        times[a][b] = times[b][a] = road.minutes
    for k in range(25):
        for i in range(25):
            for j in range(25):
                times[i][j] = min(times[i][j], times[i][k] + times[k][j])
    count = demand.randint(*config.order_range)
    bursts = (60, 150, 240)
    releases = [0] * config.initial_orders
    for _ in range(count - config.initial_orders):
        releases.append(
            demand.choice(bursts)
            if demand.random() < config.burst_fraction
            else demand.randrange(1, 10) * 30
        )
    releases.sort()
    clusters = demand.sample([0, 4, 20, 24, 6, 8, 16, 18], 3)
    orders: list[Order] = []
    for i, release in enumerate(releases):
        # Burst clusters compete geographically, with occasional expensive cross-city work.
        cluster = clusters[(release // 60) % len(clusters)]
        destination = cluster if demand.random() < 0.65 else demand.randrange(25)
        if destination == 12:
            destination = demand.choice((6, 8, 16, 18))
        urgent = demand.random() < config.urgent_fraction
        lead = demand.randint(40, 65) if urgent else demand.randint(75, 125)
        deadline = min(350, release + max(lead, int(times[12][destination]) + 12))
        value = demand.randint(20, 110)
        if config.high_value_late and release >= 120 and demand.random() < 0.38:
            value = demand.randint(180, 300)
            deadline = min(
                350, release + max(demand.randint(35, 55), int(times[12][destination]) + 12)
            )
        orders.append(
            Order(
                f"o{i:03d}",
                f"n{destination}",
                demand.randint(*config.size_range),
                deadline,
                float(value),
                demand.randint(25, 50),
                release,
            )
        )
    disruptions = [
        Disruption(
            start,
            min(start + traffic.choice((30, 60)), 360),
            road.a,
            road.b,
            traffic.choice((2, 3)),
            traffic.random() < 0.15,
        )
        for start in (30, 90, 150, 210, 270)
        for road in traffic.sample(roads, traffic.randint(2, 4))
    ]
    return World(
        tuple(orders),
        tuple(roads),
        tuple(disruptions),
        tuple(fleet.randint(*config.capacity_range) for _ in range(3)),
    )
