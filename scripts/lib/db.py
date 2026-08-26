"""Connection helper shared by every stage that touches the SQLite db."""
import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "db" / "schema.sql"
DB_PATH = Path(__file__).resolve().parents[2] / "db" / "evolutionary_markets.db"


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path = DB_PATH, schema_path: Path = SCHEMA_PATH) -> sqlite3.Connection:
    conn = connect(db_path)
    conn.executescript(schema_path.read_text())
    conn.commit()
    return conn
