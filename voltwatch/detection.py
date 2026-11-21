"""Helpers for the transparent prototype detection rules."""
import math
from datetime import datetime
from statistics import median
from .config import (WINDOW_SIZE, FREEZE_TEMP_SPREAD, FREEZE_VOLTAGE_SPREAD,
                     PEER_TEMP_SPREAD, PEER_VOLTAGE_SPREAD, MIN_FREEZE_PEERS)


def slope(points):
    if len(points) < WINDOW_SIZE or any(v is None for _, v in points):
        return None
    times = [(datetime.fromisoformat(t) - datetime.fromisoformat(points[0][0])).total_seconds() / 60 for t, _ in points]
    # Theil-Sen estimate: the median of pairwise slopes, so one spiked reading cannot fake a sustained rise.
    pairs = [(v2 - v1) / (t2 - t1) for i, (t1, (_, v1)) in enumerate(zip(times, points))
             for t2, (_, v2) in zip(times[i + 1:], points[i + 1:]) if t2 != t1]
    result = median(pairs) if pairs else None
    return result if result is not None and math.isfinite(result) else None


def frozen(histories, module, field):
    values = histories.get(module, [])[-WINDOW_SIZE:]
    if len(values) < WINDOW_SIZE or any(r.get(field) is None for _, r in values):
        return False
    spread = max(r[field] for _, r in values) - min(r[field] for _, r in values)
    peers = []
    for key, history in histories.items():
        if key == module or len(history) < WINDOW_SIZE:
            continue
        samples = history[-WINDOW_SIZE:]
        if [t for t, _ in samples] != [t for t, _ in values] or any(r.get(field) is None for _, r in samples):
            continue
        peers.append(max(r[field] for _, r in samples) - min(r[field] for _, r in samples))
    threshold, peer_threshold = (FREEZE_TEMP_SPREAD, PEER_TEMP_SPREAD) if field == 'temp_c' else (FREEZE_VOLTAGE_SPREAD, PEER_VOLTAGE_SPREAD)
    return spread <= threshold and len(peers) >= MIN_FREEZE_PEERS and median(peers) >= peer_threshold
