#!/usr/bin/env python3
"""ONE-OFF EXPORT UTILITY: Delta Lake (Ride-Sharing Analytics Platform) -> Postgres.

This is the ONLY file in this repository that touches Delta Lake, and it runs
manually, outside the deployed system. The agent backend never imports it and
never sees Delta/Spark/Kafka — it queries the resulting Postgres snapshot only.
That decoupling is deliberate: the two projects run, deploy, and fail
independently, and this script is the single, explicit bridge between them.

Reads each Gold-layer Delta table with deltalake's to_pandas() (no Spark
needed) and bulk-loads it via COPY. Per table, the load is
TRUNCATE + COPY inside one transaction, so re-running is idempotent and a
mid-export failure leaves the previous snapshot intact rather than a
half-loaded table.

Schema handling is strict: the DataFrame must contain exactly the expected
columns (see db/migrations/001_data_tables.sql). A missing or extra column
aborts the export with a diff instead of silently adapting — the schemas are
a contract, not a suggestion.

The known event_hour type mismatch (long in zone_demand, integer in
zone_demand_historical_nyc) is handled by explicit int() casts on every
event_hour value; the target column types (bigint vs integer) then govern.

Requires the "export" extra:   uv sync --extra export   (deltalake, pandas)

Usage (paths point at the other project's Gold tables; any subset is fine):
    python scripts/export_from_delta.py \
        --rides-historical       /path/to/gold/rides_historical_nyc \
        --zone-demand-historical /path/to/gold/zone_demand_historical_nyc \
        --zone-demand            /path/to/gold/zone_demand
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv

try:
    import pandas as pd
    from deltalake import DeltaTable
except ImportError:
    sys.exit(
        'Missing export dependencies. Install with: uv sync --extra export '
        '(or: pip install "deltalake>=0.17" "pandas>=2.0")'
    )

# Expected columns per target table, in COPY order. Must match
# db/migrations/001_data_tables.sql exactly.
TABLE_COLUMNS: dict[str, list[str]] = {
    "zone_demand": [
        "event_date", "event_hour", "city_zone", "ride_count", "completed_rides",
        "cancelled_rides", "gross_revenue_inr", "avg_surge_multiplier",
    ],
    "rides_historical_nyc": [
        "ride_id", "event_timestamp", "event_date", "event_hour", "city_zone",
        "distance_km", "fare_base_inr", "surge_multiplier", "payment_method",
        "vehicle_type", "is_completed", "status", "gross_fare_inr",
    ],
    "zone_demand_historical_nyc": [
        "event_date", "event_hour", "city_zone", "ride_count", "completed_rides",
        "cancelled_rides", "gross_revenue_inr", "avg_surge_multiplier",
    ],
}


def to_python_scalar(value, column: str):
    """Normalize a pandas/numpy cell to a plain Python value psycopg can COPY.

    - NaN/NaT/None -> NULL
    - numpy ints/floats/bools -> int/float/bool (.item())
    - pd.Timestamp -> naive datetime (source is timestamp_ntz, so no tz to lose)
    - event_hour -> explicit int(), regardless of source width (the documented
      long-vs-integer mismatch between the two zone_demand tables)
    """
    if value is None or (pd.api.types.is_scalar(value) and pd.isna(value)):
        return None
    if column == "event_hour":
        return int(value)
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    if isinstance(value, dt.datetime):
        return value
    if hasattr(value, "item"):  # numpy scalar
        return value.item()
    return value


def export_table(conn: psycopg.Connection, table: str, delta_path: str) -> None:
    columns = TABLE_COLUMNS[table]

    print(f"reading  {table:30s} <- {delta_path}")
    # to_pandas() materializes the whole table in memory. Fine for Gold-layer
    # aggregates and the historical subset; if the source ever outgrows RAM,
    # switch to DeltaTable.to_pyarrow_dataset() and stream record batches.
    df = DeltaTable(delta_path).to_pandas()

    expected, actual = set(columns), set(df.columns)
    if expected != actual:
        sys.exit(
            f"Schema mismatch for {table}:\n"
            f"  missing in Delta table: {sorted(expected - actual) or '-'}\n"
            f"  unexpected in Delta table: {sorted(actual - expected) or '-'}\n"
            "The schemas are a fixed contract (db/migrations/001_data_tables.sql); "
            "refusing to guess."
        )

    print(f"loading  {table:30s} {len(df):>10,} rows")
    with conn.transaction():  # TRUNCATE + COPY commit atomically per table
        conn.execute(f"TRUNCATE {table}")
        stmt = f"COPY {table} ({', '.join(columns)}) FROM STDIN"
        with conn.cursor() as cur, cur.copy(stmt) as copy:
            # itertuples in declared column order keeps COPY and schema aligned.
            for row in df[columns].itertuples(index=False, name=None):
                copy.write_row(tuple(to_python_scalar(v, c) for v, c in zip(row, columns)))

    count = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    print(f"done     {table:30s} {count:>10,} rows in Postgres")


def main() -> None:
    load_dotenv(Path(__file__).resolve().parent.parent / "backend" / ".env")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database-url", default=os.environ.get("ADMIN_DATABASE_URL"))
    parser.add_argument("--zone-demand", metavar="DELTA_PATH")
    parser.add_argument("--rides-historical", metavar="DELTA_PATH")
    parser.add_argument("--zone-demand-historical", metavar="DELTA_PATH")
    args = parser.parse_args()

    requested = {
        "zone_demand": args.zone_demand,
        "rides_historical_nyc": args.rides_historical,
        "zone_demand_historical_nyc": args.zone_demand_historical,
    }
    requested = {table: path for table, path in requested.items() if path}
    if not requested:
        parser.error("nothing to export — pass at least one --<table> DELTA_PATH")
    if not args.database_url:
        sys.exit("No database URL. Pass --database-url or set ADMIN_DATABASE_URL.")

    with psycopg.connect(args.database_url) as conn:
        for table, path in requested.items():
            export_table(conn, table, path)


if __name__ == "__main__":
    main()
