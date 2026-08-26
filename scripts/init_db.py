#!/usr/bin/env python3
"""Create db/evolutionary_markets.db from db/schema.sql. Idempotent."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.lib.db import DB_PATH, init_db

if __name__ == "__main__":
    conn = init_db()
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )]
    conn.close()
    print(f"db initialized at {DB_PATH}")
    print(f"tables: {tables}")
