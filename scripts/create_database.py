"""Create the Postgres database if it does not exist, then the schema.

Connecting to ``postgres`` first because you cannot CREATE DATABASE from inside
the database you are creating.
"""
from __future__ import annotations

import sys
from urllib.parse import urlparse

from sqlalchemy import create_engine, text

from fleet_copilot.config import settings
from fleet_copilot.storage.db import create_schema, get_engine


def main() -> int:
    url = settings.database_url
    if url.startswith("sqlite"):
        create_schema(get_engine())
        print(f"SQLite schema ready at {url}")
        return 0

    parsed = urlparse(url.replace("postgresql+psycopg://", "postgresql://"))
    dbname = (parsed.path or "/fleet_copilot").lstrip("/")
    admin_url = url.rsplit("/", 1)[0] + "/postgres"

    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": dbname},
            ).scalar()
            if exists:
                print(f"Database '{dbname}' already exists.")
            else:
                conn.execute(text(f'CREATE DATABASE "{dbname}"'))
                print(f"Created database '{dbname}'.")
    except Exception as exc:
        print(f"Could not reach Postgres at {admin_url}: {exc}", file=sys.stderr)
        print(
            "Check DATABASE_URL in .env, and that the server is running.",
            file=sys.stderr,
        )
        return 1

    create_schema(get_engine())
    print("Schema created.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
