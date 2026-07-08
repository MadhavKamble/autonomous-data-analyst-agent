#!/usr/bin/env python3
"""Provision login + password for the read-only agent_ro role.

Separated from the SQL migrations on purpose: migrations are committed to git,
credentials must never be. Migration 002 creates agent_ro as NOLOGIN with its
grants; this script flips it to LOGIN with a password. Run it once per
database (local compose, Neon) after migrations, and re-run any time you want
to rotate the credential.

Usage:
    AGENT_RO_PASSWORD=... python scripts/set_agent_password.py
    python scripts/set_agent_password.py --password ...   # (visible in shell history)
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
from psycopg import sql
from dotenv import load_dotenv


def agent_url_hint(admin_url: str) -> str:
    """The admin URL with agent_ro credentials swapped in — what to put in AGENT_DATABASE_URL."""
    parts = urlsplit(admin_url)
    host = parts.hostname or "localhost"
    port = f":{parts.port}" if parts.port else ""
    return urlunsplit(
        (parts.scheme, f"agent_ro:<password>@{host}{port}", parts.path, parts.query, "")
    )


def main() -> None:
    load_dotenv(Path(__file__).resolve().parent.parent / "backend" / ".env")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database-url", default=os.environ.get("ADMIN_DATABASE_URL"))
    parser.add_argument("--password", default=os.environ.get("AGENT_RO_PASSWORD"))
    args = parser.parse_args()

    if not args.database_url:
        sys.exit("No database URL. Pass --database-url or set ADMIN_DATABASE_URL.")
    if not args.password:
        sys.exit("No password. Set AGENT_RO_PASSWORD (preferred) or pass --password.")

    with psycopg.connect(args.database_url) as conn:
        # ALTER ROLE cannot take bind parameters; sql.Literal quotes safely.
        conn.execute(
            sql.SQL("ALTER ROLE agent_ro LOGIN PASSWORD {}").format(sql.Literal(args.password))
        )
        conn.commit()

    print("agent_ro can now log in. Set in backend/.env:")
    print(f"  AGENT_DATABASE_URL={agent_url_hint(args.database_url)}")


if __name__ == "__main__":
    main()
