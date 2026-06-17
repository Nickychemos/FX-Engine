from datetime import timedelta
from decimal import Decimal

import pytest
from hypothesis import given, strategies as st

from app.domain import rates
from app.domain.money import CurrencyError

CURRENCIES = ["USD", "EUR", "KES", "NGN"]


def test_direct_rate_favours_the_bank():
    # USD/KES mid 129.50, 50 bps spread. Customer selling USD gets the bid.
    p = rates.RateProvider.seeded(spread_bps=50)
    rate = p.effective_rate("USD", "KES")
    assert rate == Decimal("129.50") * (Decimal(1) - Decimal("0.005"))
    assert rate < Decimal("129.50")  # worse than mid, in the bank's favour


def test_inverse_rate_uses_the_ask_side():
    # KES->USD has no direct mid; derive from USD/KES at the ask.
    p = rates.RateProvider.seeded(spread_bps=50)
    rate = p.effective_rate("KES", "USD")
    expected = Decimal(1) / (Decimal("129.50") * (Decimal(1) + Decimal("0.005")))
    assert rate == expected
    assert rate < Decimal(1) / Decimal("129.50")  # worse than inverse mid


def test_cross_pair_routes_through_a_pivot_and_compounds_spread():
    # KES->NGN has no direct quote; it must route via USD (or EUR).
    p = rates.RateProvider.seeded(spread_bps=50)
    s = Decimal("0.005")
    leg1 = Decimal(1) / (Decimal("129.50") * (Decimal(1) + s))  # KES->USD
    leg2 = Decimal("1480.00") * (Decimal(1) - s)  # USD->NGN
    assert p.effective_rate("KES", "NGN") == leg1 * leg2


def test_unsupported_currency_raises():
    p = rates.RateProvider.seeded()
    with pytest.raises(CurrencyError):
        p.effective_rate("USD", "GBP")


def test_no_rate_available_when_no_path_exists():
    empty = rates.RateProvider(mids={}, last_updated=rates._now())
    with pytest.raises(rates.NoRateAvailable):
        empty.effective_rate("USD", "KES")


def test_stale_rates_are_detected_and_rejected():
    old = rates._now() - timedelta(seconds=600)
    p = rates.RateProvider.seeded()
    p.last_updated = old
    assert p.is_stale(300) is True
    with pytest.raises(rates.RatesStale):
        p.require_fresh(300)


def test_fresh_rates_pass():
    p = rates.RateProvider.seeded()
    assert p.is_stale(300) is False
    p.require_fresh(300)  # does not raise


def test_refresh_failure_keeps_last_good_snapshot():
    def broken_fetch():
        raise ConnectionError("rate source down")

    p = rates.RateProvider.seeded()
    p.fetcher = broken_fetch
    before_mids = dict(p.mids)
    before_updated = p.last_updated

    with pytest.raises(rates.RateSourceError):
        p.refresh()

    assert p.mids == before_mids  # unchanged
    assert p.last_updated == before_updated  # unchanged


def test_refresh_success_updates_snapshot():
    new = {("USD", "KES"): Decimal("130.00")}
    p = rates.RateProvider.seeded()
    p.fetcher = lambda: new
    p.refresh()
    assert p.mids == new


@given(
    pair=st.lists(st.sampled_from(CURRENCIES), min_size=2, max_size=2, unique=True)
)
def test_round_trip_never_profits_the_customer(pair):
    # Converting A->B and back B->A must lose value to the spread, every time.
    frm, to = pair
    p = rates.RateProvider.seeded(spread_bps=50)
    there = p.effective_rate(frm, to)
    back = p.effective_rate(to, frm)
    assert there * back < Decimal(1)
