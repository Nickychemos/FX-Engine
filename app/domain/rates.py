"""Exchange rates: mid rates, buy/sell spread, cross-pair routing, and freshness.

The bank profits from the spread, so every conversion gives the customer the
worse side of the mid. Spread is applied per leg, so a cross pair (routed through
a pivot) naturally carries the spread twice. All rate math is Decimal and is kept
at full precision here; rounding to a currency's minor units happens only when a
final amount is produced, not in this module.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from app.domain.money import require_currency

# Mid rates as (base, quote) -> mid, meaning `mid` units of quote per 1 base.
SEED_MID: dict[tuple[str, str], Decimal] = {
    ("USD", "EUR"): Decimal("0.92"),
    ("USD", "KES"): Decimal("129.50"),
    ("USD", "NGN"): Decimal("1480.00"),
    ("EUR", "KES"): Decimal("140.75"),
    ("EUR", "NGN"): Decimal("1608.50"),
}

# Cross pairs without a direct or inverse quote route through these, in order.
PIVOTS: tuple[str, ...] = ("USD", "EUR")


class RateError(Exception):
    """Base class for rate problems."""


class NoRateAvailable(RateError):
    """No direct, inverse, or routed path exists for the requested pair."""


class RatesStale(RateError):
    """Rates are older than the allowed staleness; refuse to quote (fail closed)."""


class RateSourceError(RateError):
    """The upstream rate source failed; the last-good snapshot is kept."""


MidLookup = Callable[[str, str], Decimal | None]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _spread_fraction(spread_bps: int) -> Decimal:
    return Decimal(spread_bps) / Decimal(10000)


def _leg_rate(get_mid: MidLookup, frm: str, to: str, s: Decimal) -> Decimal | None:
    """Single-hop rate frm -> to (units of `to` per 1 `frm`), bank-favouring.

    If a direct mid exists the customer is selling `frm`, so the bank buys it at
    the bid (mid below par). If only the inverse mid exists the customer is buying
    `to`, so the bank sells it at the ask (mid above par). Either way the customer
    gets the worse side.
    """
    direct = get_mid(frm, to)
    if direct is not None:
        return direct * (Decimal(1) - s)

    inverse = get_mid(to, frm)
    if inverse is not None:
        return Decimal(1) / (inverse * (Decimal(1) + s))

    return None


def effective_rate(
    get_mid: MidLookup, frm: str, to: str, spread_bps: int
) -> Decimal:
    """Resolve frm -> to: direct, then inverse, then via USD, then via EUR."""
    require_currency(frm)
    require_currency(to)
    s = _spread_fraction(spread_bps)

    direct = _leg_rate(get_mid, frm, to, s)
    if direct is not None:
        return direct

    for pivot in PIVOTS:
        if pivot in (frm, to):
            continue
        leg1 = _leg_rate(get_mid, frm, pivot, s)
        leg2 = _leg_rate(get_mid, pivot, to, s)
        if leg1 is not None and leg2 is not None:
            return leg1 * leg2

    raise NoRateAvailable(f"no rate available for {frm}/{to}")


@dataclass
class RateProvider:
    """Holds a snapshot of mid rates and when it was last refreshed."""

    mids: dict[tuple[str, str], Decimal]
    last_updated: datetime
    spread_bps: int = 50
    fetcher: Callable[[], dict[tuple[str, str], Decimal]] | None = field(
        default=None, repr=False
    )

    @classmethod
    def seeded(cls, spread_bps: int = 50, now: datetime | None = None) -> "RateProvider":
        return cls(mids=dict(SEED_MID), last_updated=now or _now(), spread_bps=spread_bps)

    def get_mid(self, base: str, quote: str) -> Decimal | None:
        return self.mids.get((base, quote))

    def effective_rate(self, frm: str, to: str) -> Decimal:
        return effective_rate(self.get_mid, frm, to, self.spread_bps)

    def age_seconds(self, now: datetime | None = None) -> float:
        return ((now or _now()) - self.last_updated).total_seconds()

    def is_stale(self, max_staleness_seconds: int, now: datetime | None = None) -> bool:
        return self.age_seconds(now) > max_staleness_seconds

    def require_fresh(
        self, max_staleness_seconds: int, now: datetime | None = None
    ) -> None:
        if self.is_stale(max_staleness_seconds, now):
            raise RatesStale(
                f"rates are stale (age {self.age_seconds(now):.0f}s, "
                f"limit {max_staleness_seconds}s)"
            )

    def refresh(self, now: datetime | None = None) -> None:
        """Refresh from the source. On failure keep the last-good snapshot."""
        if self.fetcher is None:
            self.mids = dict(SEED_MID)
            self.last_updated = now or _now()
            return
        try:
            new_mids = self.fetcher()
        except Exception as exc:  # keep last-good; do not mutate state
            raise RateSourceError(str(exc)) from exc
        self.mids = new_mids
        self.last_updated = now or _now()

    def snapshot(self) -> dict:
        s = _spread_fraction(self.spread_bps)
        rates = {}
        for (base, quote), mid in sorted(self.mids.items()):
            rates[f"{base}/{quote}"] = {
                "mid": str(mid),
                "buy": str(mid * (Decimal(1) - s)),
                "sell": str(mid * (Decimal(1) + s)),
            }
        return {
            "rates": rates,
            "last_updated": self.last_updated.isoformat(),
            "spread_bps": self.spread_bps,
        }
