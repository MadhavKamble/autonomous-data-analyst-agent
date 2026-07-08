"""Shared fixtures.

Tests run against the docker-compose Postgres (see repo root README quickstart
steps 1-5: migrations applied, mock data seeded, agent_ro password set, RAG
index built), never against Neon — the read-only-role tests specifically need
a database we're allowed to provision roles on, and CI must never touch prod.
"""

from __future__ import annotations

import psycopg
import pytest

from app.config import Settings, get_settings

DATA_TABLES = ("zone_demand", "rides_historical_nyc", "zone_demand_historical_nyc")


@pytest.fixture(scope="session")
def settings() -> Settings:
    return get_settings()


@pytest.fixture(scope="session")
def admin_database_url(settings: Settings) -> str:
    return settings.admin_database_url


@pytest.fixture(scope="session")
def agent_database_url(settings: Settings) -> str:
    return settings.agent_database_url


@pytest.fixture(scope="session", autouse=True)
def _require_seeded_database(admin_database_url: str) -> None:
    """Fail fast with a clear message if the dev DB isn't set up, instead of
    every test failing separately with a confusing connection/empty-table error."""
    try:
        with psycopg.connect(admin_database_url) as conn:
            counts = {
                table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                for table in DATA_TABLES
            }
    except psycopg.Error as error:
        pytest.exit(
            f"Could not reach/read the dev database at {admin_database_url}: {error}\n"
            "Run: docker compose up -d && python scripts/run_migrations.py "
            "&& python db/seed/seed_mock_data.py (see README Quickstart).",
            returncode=1,
        )
    empty = [table for table, count in counts.items() if count == 0]
    if empty:
        pytest.exit(
            f"Tables exist but are empty: {empty}. Run db/seed/seed_mock_data.py first.",
            returncode=1,
        )
