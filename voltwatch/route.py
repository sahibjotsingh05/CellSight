"""Simulated buses driving back and forth along real ETS routes.

routes.json holds full terminal-to-terminal paths simplified (~9 m tolerance) from the
Edmonton Transit Service GTFS shapes (feed 261023, City of Edmonton Open Data).
Only the vehicle movement is simulated.
"""
import json
import math
from datetime import datetime, timezone
from pathlib import Path

SPEED_KMH = 30
ROUTES = json.loads((Path(__file__).parent / 'routes.json').read_text())


def route_for(index):
    """The route the utility assigns to the bus at this fleet position."""
    return dict(ROUTES[index % len(ROUTES)])


def _metres(a, b):
    dy = (b[0] - a[0]) * 111_320
    dx = (b[1] - a[1]) * 111_320 * math.cos(math.radians(a[0]))
    return math.hypot(dx, dy)


class RouteState:
    def __init__(self, route_index=0):
        self.info = route_for(route_index)
        self.points = self.info['path']
        self.lengths = [_metres(a, b) for a, b in zip(self.points, self.points[1:])]
        self.total = sum(self.lengths)
        # Buses sharing a route (fleets over five) start at different points on it.
        self.distance = (route_index // len(ROUTES)) * 0.37 * 2 * self.total
        self.last_time = None
        self.vehicle_state = 'moving'
        self.demo_override = False

    def position(self, now):
        if self.last_time is not None and self.vehicle_state == 'moving':
            self.distance += max(0.0, now - self.last_time) * SPEED_KMH / 3.6
        self.last_time = now
        lap = self.distance % (2 * self.total)
        outbound = lap <= self.total
        along = lap if outbound else 2 * self.total - lap
        segment = 0
        while segment < len(self.lengths) - 1 and along > self.lengths[segment]:
            along -= self.lengths[segment]
            segment += 1
        a, b, length = self.points[segment], self.points[segment + 1], self.lengths[segment]
        t = min(1.0, along / length) if length else 0.0
        lat, lng = a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t
        start, end = (a, b) if outbound else (b, a)
        heading = math.degrees(math.atan2((end[1] - start[1]) * math.cos(math.radians(lat)), end[0] - start[0]))
        return {'lat': lat, 'lng': lng, 'heading_deg': (heading + 360) % 360,
                'speed_kmh': SPEED_KMH if self.vehicle_state == 'moving' else 0,
                'observed_at': datetime.now(timezone.utc).isoformat(), 'source': 'simulated'}

    def set_state(self, state, demo_override, now):
        self.position(now)
        self.vehicle_state = state
        self.demo_override = demo_override
