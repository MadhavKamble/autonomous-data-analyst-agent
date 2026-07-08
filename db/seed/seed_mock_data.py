#!/usr/bin/env python3
"""Seed the database with realistic MOCK ride-sharing data.

Standalone by design: development and the test suite must never require the
upstream Delta Lake project. Real data is loaded instead (and this mock data
replaced) by scripts/export_from_delta.py.

What "realistic" means here, so demo questions produce sensible answers:

- Hourly demand follows a commuter curve (morning/evening peaks, quiet 2-5am).
- Fares correlate with distance; surge rises in peak hours; gross fare is
  base x surge for completed rides and 0.0 for cancelled ones.
- Airport zones skew towards long trips.
- zone_demand_historical_nyc is AGGREGATED FROM the generated rides inside the
  database, so the row-level and aggregate tables are mutually consistent —
  a cross-check the Critic agent (and an interviewer) can actually verify.

Everything is driven by a seeded RNG (--seed), so runs are reproducible and
tests can assert against stable data.

Categorical values (zones, vehicle types, payment methods, statuses) are
plausible PLACEHOLDERS, not values copied from the real pipeline — the real
value sets arrive with the export. Dates are fixed, not relative to "today",
for reproducibility.

Usage:
    python db/seed/seed_mock_data.py                    # ADMIN_DATABASE_URL from env/.env
    python db/seed/seed_mock_data.py --days 30 --rides-per-day 2000 --seed 42
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import random
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Generation parameters
# ---------------------------------------------------------------------------

# Historical NYC mock: real TLC zone names, weighted by plausible popularity.
NYC_ZONES: list[tuple[str, float]] = [
    ("Midtown Center", 10.0),
    ("Times Sq/Theatre District", 9.0),
    ("Upper East Side North", 8.0),
    ("JFK Airport", 7.0),
    ("East Village", 7.0),
    ("LaGuardia Airport", 6.0),
    ("Financial District North", 6.0),
    ("Harlem", 5.0),
    ("Williamsburg (North Side)", 5.0),
    ("Brooklyn Heights", 4.0),
    ("Astoria", 4.0),
    ("Park Slope", 3.0),
]
AIRPORT_ZONES = {"JFK Airport", "LaGuardia Airport"}

# Synthetic live-simulator mock (zone_demand): the upstream simulator models an
# Indian city (hence INR), so its zones are Indian.
SIM_ZONES: list[tuple[str, float]] = [
    ("Koramangala", 9.0),
    ("Indiranagar", 8.0),
    ("Whitefield", 7.0),
    ("HSR Layout", 7.0),
    ("MG Road", 6.0),
    ("Electronic City", 5.0),
    ("Hebbal", 4.0),
    ("Jayanagar", 4.0),
]

VEHICLE_TYPES = [("economy", 0.55), ("comfort", 0.25), ("premium", 0.12), ("xl", 0.08)]
PAYMENT_METHODS = [("upi", 0.35), ("card", 0.30), ("cash", 0.20), ("wallet", 0.15)]
RIDE_STATUSES = [
    ("completed", 0.87),
    ("cancelled_by_rider", 0.08),
    ("cancelled_by_driver", 0.05),
]

# Relative demand by hour of day (index = hour). Commuter curve: peaks around
# 8-9am and 6-7pm, trough at 3-4am.
HOUR_WEIGHTS = [
    1.0, 0.5, 0.3, 0.2, 0.3, 0.8,   # 00-05
    2.0, 4.0, 6.0, 5.0, 3.5, 3.0,   # 06-11
    3.5, 3.5, 3.0, 3.5, 4.5, 6.0,   # 12-17
    6.5, 5.5, 4.5, 3.5, 2.5, 1.5,   # 18-23
]

# Mean surge by hour: follows demand but never below 1.0.
SURGE_MEAN_BY_HOUR = [1.0 + 0.25 * (w / max(HOUR_WEIGHTS)) ** 2 * 4 for w in HOUR_WEIGHTS]

# Fixed date ranges (not "today"-relative) so reruns with the same seed produce
# byte-identical data.
HISTORICAL_END_DATE = dt.date(2026, 6, 30)
SIM_END_DATE = dt.date(2026, 7, 7)
SIM_DAYS = 7

RIDES_COLUMNS = [
    "ride_id", "event_timestamp", "event_date", "event_hour", "city_zone",
    "distance_km", "fare_base_inr", "surge_multiplier", "payment_method",
    "vehicle_type", "is_completed", "status", "gross_fare_inr",
]
ZONE_DEMAND_COLUMNS = [
    "event_date", "event_hour", "city_zone", "ride_count", "completed_rides",
    "cancelled_rides", "gross_revenue_inr", "avg_surge_multiplier",
]


def weighted_choice(rng: random.Random, options: list[tuple[str, float]]) -> str:
    values, weights = zip(*options)
    return rng.choices(values, weights=weights, k=1)[0]


# ---------------------------------------------------------------------------
# Row generators
# ---------------------------------------------------------------------------

def generate_rides(rng: random.Random, days: int, rides_per_day: int) -> list[tuple]:
    """Row-level mock rides for rides_historical_nyc."""
    rows: list[tuple] = []
    start_date = HISTORICAL_END_DATE - dt.timedelta(days=days - 1)

    for day_offset in range(days):
        date = start_date + dt.timedelta(days=day_offset)
        # Weekends run ~20% hotter, plus mild day-to-day noise.
        day_factor = (1.2 if date.weekday() >= 5 else 1.0) * rng.uniform(0.9, 1.1)
        n_rides = int(rides_per_day * day_factor)

        hours = rng.choices(range(24), weights=HOUR_WEIGHTS, k=n_rides)
        for hour in hours:
            zone = weighted_choice(rng, NYC_ZONES)

            # Distance: lognormal gives the right long-tail shape (many short
            # hops, few long hauls); airports add a fixed haul on top.
            distance = rng.lognormvariate(1.1, 0.6)
            if zone in AIRPORT_ZONES:
                distance += rng.uniform(8.0, 20.0)
            distance = round(min(max(distance, 0.8), 45.0), 2)

            fare_base = round(max(60.0 + 14.0 * distance + rng.gauss(0, 12.0), 50.0), 2)
            surge = round(min(max(rng.gauss(SURGE_MEAN_BY_HOUR[hour], 0.25), 1.0), 3.0), 2)

            status = weighted_choice(rng, RIDE_STATUSES)
            is_completed = status == "completed"
            gross_fare = round(fare_base * surge, 2) if is_completed else 0.0

            timestamp = dt.datetime(
                date.year, date.month, date.day, hour, rng.randrange(60), rng.randrange(60)
            )
            rows.append((
                f"{rng.getrandbits(64):016x}",       # deterministic unique-ish id
                timestamp,
                date.isoformat(),                     # event_date is a string upstream
                hour,
                zone,
                distance,
                fare_base,
                surge,
                weighted_choice(rng, PAYMENT_METHODS),
                weighted_choice(rng, VEHICLE_TYPES),
                is_completed,
                status,
                gross_fare,
            ))
    return rows


def generate_zone_demand(rng: random.Random) -> list[tuple]:
    """Hourly aggregates for zone_demand (synthetic live-simulator path)."""
    rows: list[tuple] = []
    start_date = SIM_END_DATE - dt.timedelta(days=SIM_DAYS - 1)

    for day_offset in range(SIM_DAYS):
        date = start_date + dt.timedelta(days=day_offset)
        for hour in range(24):
            for zone, popularity in SIM_ZONES:
                expected = HOUR_WEIGHTS[hour] * popularity * rng.uniform(0.7, 1.3)
                ride_count = max(int(expected), 0)
                if ride_count == 0:
                    continue  # the simulator emits no row for a dead zone-hour
                completed = sum(1 for _ in range(ride_count) if rng.random() < 0.88)
                cancelled = ride_count - completed
                surge = round(min(max(rng.gauss(SURGE_MEAN_BY_HOUR[hour], 0.15), 1.0), 3.0), 2)
                avg_fare = rng.uniform(150.0, 400.0)
                rows.append((
                    date.isoformat(),
                    hour,
                    zone,
                    ride_count,
                    completed,
                    cancelled,
                    round(completed * avg_fare * surge, 2),
                    surge,
                ))
    return rows


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

# Aggregating the historical table FROM the generated rides (rather than
# generating it independently) guarantees the two NYC tables tell one
# consistent story. Semantics assumed: ride_count counts all rides,
# gross_revenue_inr sums completed rides only, avg surge averages all rides.
AGGREGATE_HISTORICAL_SQL = """
    INSERT INTO zone_demand_historical_nyc
        (event_date, event_hour, city_zone, ride_count, completed_rides,
         cancelled_rides, gross_revenue_inr, avg_surge_multiplier)
    SELECT
        event_date,
        event_hour,
        city_zone,
        count(*),
        count(*) FILTER (WHERE is_completed),
        count(*) FILTER (WHERE NOT is_completed),
        round(coalesce(sum(gross_fare_inr) FILTER (WHERE is_completed), 0)::numeric, 2),
        round(avg(surge_multiplier)::numeric, 4)
    FROM rides_historical_nyc
    GROUP BY event_date, event_hour, city_zone
"""


def copy_rows(conn: psycopg.Connection, table: str, columns: list[str], rows: list[tuple]) -> None:
    """Bulk-load via COPY — orders of magnitude faster than executemany."""
    stmt = f"COPY {table} ({', '.join(columns)}) FROM STDIN"
    with conn.cursor() as cur, cur.copy(stmt) as copy:
        for row in rows:
            copy.write_row(row)


def seed(database_url: str, days: int, rides_per_day: int, seed_value: int) -> None:
    rng = random.Random(seed_value)
    rides = generate_rides(rng, days, rides_per_day)
    zone_demand = generate_zone_demand(rng)

    with psycopg.connect(database_url) as conn:
        # Single transaction: a rerun either fully replaces the data or
        # changes nothing.
        conn.execute(
            "TRUNCATE rides_historical_nyc, zone_demand_historical_nyc, zone_demand"
        )
        copy_rows(conn, "rides_historical_nyc", RIDES_COLUMNS, rides)
        conn.execute(AGGREGATE_HISTORICAL_SQL)
        copy_rows(conn, "zone_demand", ZONE_DEMAND_COLUMNS, zone_demand)
        conn.commit()

        for table in ("rides_historical_nyc", "zone_demand_historical_nyc", "zone_demand"):
            count = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            print(f"{table:30s} {count:>8,} rows")

    print(f"seeded (seed={seed_value}, days={days}, rides/day~{rides_per_day})")


def main() -> None:
    load_dotenv(Path(__file__).resolve().parents[2] / "backend" / ".env")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database-url", default=os.environ.get("ADMIN_DATABASE_URL"))
    parser.add_argument("--days", type=int, default=30, help="days of historical rides")
    parser.add_argument("--rides-per-day", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=42, help="RNG seed for reproducibility")
    args = parser.parse_args()

    if not args.database_url:
        sys.exit("No database URL. Pass --database-url or set ADMIN_DATABASE_URL.")
    seed(args.database_url, args.days, args.rides_per_day, args.seed)


if __name__ == "__main__":
    main()
