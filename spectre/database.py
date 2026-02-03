"""DuckDB database management for Spectre."""

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

import duckdb

logger = logging.getLogger(__name__)


class DatabaseConnection:
    """Context manager for DuckDB connection."""

    def __init__(self, database_path: Optional[str] = None):
        if database_path is None:
            database_path = os.getenv(
                "SPECTRE_DB_PATH", "./data/spectre.duckdb"
            )
        self.database_path = database_path
        self._conn = None

    def __enter__(self):
        
        data_dir = Path(self.database_path).parent
        data_dir.mkdir(parents=True, exist_ok=True)

        logger.debug(f"Connecting to DuckDB at {self.database_path}")
        self._conn = duckdb.connect(self.database_path)
        return self._conn

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._conn is not None:
            self._conn.close()
        return False  


def init_database(database_path: Optional[str] = None) -> None:
    """Initialize the database schema (create tables if not exist)."""
    with DatabaseConnection(database_path) as conn:
        
        conn.execute("""
            CREATE TABLE IF NOT EXISTS blobs (
                hash TEXT PRIMARY KEY,
                body JSON NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        
        conn.execute("""
            CREATE TABLE IF NOT EXISTS captures (
                id UUID PRIMARY KEY,
                session_id TEXT NOT NULL,
                url TEXT NOT NULL,
                method TEXT NOT NULL,
                headers JSON,
                status INTEGER,
                blob_hash TEXT REFERENCES blobs(hash),
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        
        conn.execute("""
            CREATE TABLE IF NOT EXISTS resources (
                name TEXT PRIMARY KEY,
                url_pattern TEXT NOT NULL,
                method TEXT NOT NULL,
                primary_key TEXT
            )
        """)

        
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_captures_url ON captures(url)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_captures_timestamp "
            "ON captures(timestamp)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_captures_session "
            "ON captures(session_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_blobs_hash ON blobs(hash)"
        )

        logger.info("Database schema initialized")


def hash_body(body: bytes) -> str:
    """Compute SHA256 hash of a response body."""
    return hashlib.sha256(body).hexdigest()


def insert_blob(
    conn: duckdb.DuckDBPyConnection,
    body: bytes,
    hash_value: Optional[str] = None,
) -> str:
    """
    Insert a blob if not already present, return its hash.

    Args:
        conn: DuckDB connection.
        body: Raw bytes of the JSON body.
        hash_value: Pre‑computed hash (optional). If not provided, will be
                    computed from body.

    Returns:
        Hash string (SHA256).
    """
    if hash_value is None:
        hash_value = hash_body(body)

    
    exists = conn.execute(
        "SELECT 1 FROM blobs WHERE hash = ?", (hash_value,)
    ).fetchone()
    if exists:
        return hash_value

    
    conn.execute(
        "INSERT INTO blobs (hash, body) VALUES (?, ?)",
        (hash_value, body.decode("utf-8")),
    )
    logger.debug(f"Inserted new blob {hash_value[:8]}")
    return hash_value


def insert_capture(
    conn: duckdb.DuckDBPyConnection,
    session_id: str,
    url: str,
    method: str,
    headers: Optional[Dict[str, Any]],
    status: int,
    body: bytes,
    timestamp: Optional[str] = None,
) -> str:
    """
    Store a captured response.

    Args:
        conn: DuckDB connection.
        session_id: Identifier for the capture session.
        url: Full request URL.
        method: HTTP method.
        headers: Response headers as dict.
        status: HTTP status code.
        body: Raw response body.
        timestamp: Optional timestamp (ISO string). Defaults to now.

    Returns:
        UUID of the inserted capture record.
    """
    
    blob_hash = insert_blob(conn, body)

    capture_id = str(uuid4())

    sql = """
        INSERT INTO captures
            (id, session_id, url, method, headers, status, blob_hash, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """
    params = (
        capture_id,
        session_id,
        url,
        method,
        json.dumps(headers) if headers else None,
        status,
        blob_hash,
        timestamp,
    )
    conn.execute(sql, params)
    logger.debug(f"Inserted capture {capture_id} for {url}")
    return capture_id


def get_captures_by_pattern(
    conn: duckdb.DuckDBPyConnection,
    url_pattern: str,
    method: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> List[Tuple[Any, ...]]:
    """
    Retrieve captures matching a URL pattern.

    Args:
        conn: DuckDB connection.
        url_pattern: SQL LIKE pattern (use % wildcard).
        method: Optional HTTP method filter.
        limit: Maximum number of rows.
        offset: Pagination offset.

    Returns:
        List of rows (each row is a tuple).
    """
    query = """
        SELECT c.*, b.body
        FROM captures c
        JOIN blobs b ON c.blob_hash = b.hash
        WHERE c.url LIKE ?
    """
    params = [url_pattern]
    if method:
        query += " AND c.method = ?"
        params.append(method)
    query += " ORDER BY c.timestamp DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    return conn.execute(query, params).fetchall()


def get_distinct_urls(
    conn: duckdb.DuckDBPyConnection, limit: int = 1000
) -> List[str]:
    """Return a list of distinct captured URLs."""
    rows = conn.execute(
        "SELECT DISTINCT url FROM captures ORDER BY url LIMIT ?",
        (limit,),
    ).fetchall()
    return [row[0] for row in rows]


def cleanup_old_captures(
    conn: duckdb.DuckDBPyConnection,
    older_than_days: int = 30,
) -> int:
    """
    Delete captures older than specified days, cleaning up orphaned blobs.

    Returns number of deleted capture rows.
    """
    
    result = conn.execute(
    """
    DELETE FROM captures
    WHERE timestamp < CURRENT_TIMESTAMP - make_interval(days := ?)
    RETURNING id, blob_hash
    """,
    (older_than_days,),).fetchall()

    
    conn.execute("""
        DELETE FROM blobs
        WHERE hash NOT IN (SELECT DISTINCT blob_hash FROM captures)
    """)
    logger.info(
        f"Cleaned up {deleted} captures older than {older_than_days} days"
    )
    return deleted


if __name__ == "__main__":
    
    logging.basicConfig(level=logging.INFO)
    init_database()
    print("Database initialized successfully.")