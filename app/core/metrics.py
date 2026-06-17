"""Prometheus metrics for the FX engine."""
from __future__ import annotations

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

QUOTES_CREATED = Counter("fx_quotes_created", "Quotes created")
EXECUTES = Counter("fx_executes", "Execute attempts by outcome", ["outcome"])
EXECUTE_LATENCY = Histogram("fx_execute_seconds", "Execute latency in seconds")
RATE_REFRESHES = Counter("fx_rate_refreshes", "Rate refreshes by outcome", ["outcome"])
RATE_STALENESS = Gauge(
    "fx_rate_staleness_seconds", "Age of the current rate snapshot in seconds"
)


def render() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
