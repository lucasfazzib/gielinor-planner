"""Price lookups for recommendations (DECISIONS.md D12).

- Buying uses the instant-buy price (`high` from /latest) and selling the instant-sell price (`low`): the
  conservative side of the spread in both directions, so profits are never overstated.
- A side is only used if it traded in the last 48 hours (stale prices of rarely traded items are not prices).
- A "crossed" market (instant-sell more than 20% above instant-buy) means a few odd trades on a thin item:
  the sell side is ignored. Seen in practice: Moonlight mead(m) high 30,000 / low 100,000 made a cooking recipe
  look like +1,659 gp per XP.
- An item nobody sold in the last hour (/1h volume = 0) has no reliable sell price either.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import duckdb

MAX_AGE = timedelta(hours=48)
CROSSED_TOLERANCE = 1.2


@dataclass(frozen=True)
class Price:
    buy: int | None  # what you pay (instant buy)
    sell: int | None  # what you get (instant sell)


COINS = Price(1, 1)  # coins are not traded on the GE; one coin is worth one coin


def clean(
    high: int | None,
    high_time: datetime | None,
    low: int | None,
    low_time: datetime | None,
    now: datetime | None = None,
    *,
    sold_last_hour: int | None = None,
) -> Price:
    now = now or datetime.now(UTC)
    buy = high if high is not None and high_time is not None and now - high_time <= MAX_AGE else None
    sell = low if low is not None and low_time is not None and now - low_time <= MAX_AGE else None
    if buy is not None and sell is not None and sell > buy * CROSSED_TOLERANCE:
        sell = None
    if sold_last_hour == 0:  # nobody sold this in the last hour: a quoted sell price is not one you can rely on
        sell = None
    return Price(buy, sell)


def price_book(
    con: duckdb.DuckDBPyConnection, now: datetime | None = None
) -> tuple[dict[str, Price], dict[int, Price]]:
    by_name, by_id = {"Coins": COINS}, {}
    for item_id, name, high, high_time, low, low_time, sold in con.execute(
        "select item_id, name, high, high_time, low, low_time, volume_low_1h from prices"
    ).fetchall():
        p = clean(high, high_time, low, low_time, now, sold_last_hour=sold)
        by_name[name] = p
        by_id[item_id] = p
    return by_name, by_id
