#!/usr/bin/env python3
"""Apply SQL migrations in db/migrations/ in filename order.

Deliberately minimal instead of Alembic: the schema is three snapshot tables
plus a handful of infrastructure tables, there are no evolving ORM models, and
plain numbered SQL files are easier to review. Consequences of that choice:

- Forward-only. There are no down migrations; recovering from a bad migration
  means writing a new one (or, locally, recreating the docker volume).
- Each migration file runs inside its own transaction and is recorded in
  schema_migrations, so re-running this script is idempotent and a failed
  migration never half-applies.

Usage:
    python scripts/run_migrations.py            # uses ADMIN_DATABASE_URL
    python scripts/run_migrations.py --database-url postgresql://...
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "db" / "migrations"


def resolve_database_url(cli_value: str | None) -> str:
    """CLI flag wins; otherwise ADMIN_DATABASE_URL from the environment/.env."""
    load_dotenv(Path(__file__).resolve().parent.parent / "backend" / ".env")
    url = cli_value or os.environ.get("ADMIN_DATABASE_URL")
    if not url:
        sys.exit(
            "No database URL. Pass --database-url or set ADMIN_DATABASE_URL "
            "(see backend/.env.example)."
        )
    return url


def apply_migrations(database_url: str) -> None:
    migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not migration_files:
        sys.exit(f"No .sql files found in {MIGRATIONS_DIR}")

    with psycopg.connect(database_url) as conn:
        # The ledger table itself must exist before we can consult it.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                filename   text PRIMARY KEY,
                applied_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
        conn.commit()

        applied = {
            row[0] for row in conn.execute("SELECT filename FROM schema_migrations").fetchall()
        }

        for path in migration_files:
            if path.name in applied:
                print(f"skip   {path.name} (already applied)")
                continue
            print(f"apply  {path.name}")
            try:
                # One transaction per file: the SQL and its ledger entry
                # commit together or not at all.
                conn.execute(path.read_text())
                conn.execute(
                    "INSERT INTO schema_migrations (filename) VALUES (%s)", (path.name,)
                )
                conn.commit()
            except Exception:
                conn.rollback()
                print(f"FAILED {path.name} — rolled back, stopping.", file=sys.stderr)
                raise

    print("migrations up to date")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database-url", help="overrides ADMIN_DATABASE_URL")
    args = parser.parse_args()
    apply_migrations(resolve_database_url(args.database_url))


if __name__ == "__main__":
    main()
