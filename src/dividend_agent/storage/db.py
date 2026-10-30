"""SQLite connection helpers with WAL mode and migrations."""

from __future__ import annotations

import sqlite3
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def connect(sqlite_path: str | Path) -> sqlite3.Connection:
    """Open SQLite with WAL, foreign keys, and Row factory."""
    path = Path(sqlite_path)
    if path.parent and str(path.parent) not in ("", "."):
        path.parent.mkdir(parents=True, exist_ok=True)
    # isolation_level=None => autocommit; repository statements persist immediately.
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def _migration_files() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def current_schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    ).fetchone()
    if row is None:
        return 0
    version_row = conn.execute(
        "SELECT COALESCE(MAX(version), 0) AS v FROM schema_migrations"
    ).fetchone()
    return int(version_row["v"]) if version_row else 0


def apply_migrations(conn: sqlite3.Connection) -> list[int]:
    """Apply pending SQL migrations. Returns applied version numbers.

    Note: ``executescript`` auto-commits; each migration file is applied as its
    own script, then the version row is recorded in a separate statement.
    """
    applied: list[int] = []
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """
    )
    current = current_schema_version(conn)
    for path in _migration_files():
        stem = path.stem  # e.g. 001_initial
        version = int(stem.split("_", 1)[0])
        if version <= current:
            continue
        sql = path.read_text(encoding="utf-8")
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (?, datetime('now'))",
            (version,),
        )
        conn.commit()
        applied.append(version)
        current = version
    return applied


def open_migrated(sqlite_path: str | Path) -> sqlite3.Connection:
    """Connect and apply migrations."""
    conn = connect(sqlite_path)
    apply_migrations(conn)
    return conn
