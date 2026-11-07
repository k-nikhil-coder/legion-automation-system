"""
Legion Automation System - SQLite Database Module
Manages items, deduplication, and approval states.
"""

import sqlite3
import os
import sys
import urllib.parse
from contextlib import contextmanager
from datetime import datetime
from typing import List, Dict, Optional, Any, Generator

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "legion.db")

def normalize_url(url: str) -> str:
    """
    Normalizes URL for deduplication by:
    - Lowercasing scheme and netloc
    - Stripping trailing slashes
    - Removing marketing/tracking query parameters (utm_*, ref, source, etc.)
    """
    if not url:
        return ""
    try:
        parsed = urllib.parse.urlparse(url.strip())
        tracking_prefixes = ("utm_", "ref", "source", "fbclid", "gclid")
        query_pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=False)
        clean_pairs = [
            (k, v) for k, v in query_pairs
            if not any(k.lower().startswith(tp) for tp in tracking_prefixes)
        ]
        clean_pairs.sort()
        new_query = urllib.parse.urlencode(clean_pairs)
        path = parsed.path.rstrip("/") if parsed.path != "/" else "/"
        return urllib.parse.urlunparse((
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            path,
            parsed.params,
            new_query,
            ""  # remove fragment
        ))
    except Exception:
        return url.strip()

def get_connection(db_path: str = DB_PATH) -> sqlite3.Connection:
    """Returns a new raw SQLite connection configured with WAL mode and busy timeout."""
    conn = sqlite3.connect(db_path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
    except Exception:
        pass
    return conn

@contextmanager
def get_db_connection(db_path: str = DB_PATH) -> Generator[sqlite3.Connection, None, None]:
    """
    Context manager that yields a connection and ensures it is safely closed.
    Prevents lingering file locks on Windows and resource leaks.
    """
    conn = get_connection(db_path)
    try:
        yield conn
    finally:
        conn.close()

def init_db(db_path: str = DB_PATH):
    """Initializes the database schema."""
    with get_db_connection(db_path) as conn:
        with conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    category TEXT NOT NULL,
                    title TEXT NOT NULL,
                    url TEXT UNIQUE NOT NULL,
                    summary TEXT,
                    date_str TEXT,
                    deadline TEXT,
                    location TEXT,
                    eligibility TEXT,
                    draft_text TEXT,
                    status TEXT DEFAULT 'pending',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    badge TEXT
                );
            """)
            try:
                cursor.execute("ALTER TABLE items ADD COLUMN badge TEXT;")
            except Exception:
                pass
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_items_url ON items(url);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_items_status ON items(status);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_items_category ON items(category);")

def item_exists(url: str, db_path: str = DB_PATH) -> bool:
    """Checks if an item with the given URL (raw or normalized) has already been collected."""
    if not url:
        return False
    raw_url = url.strip()
    norm_url = normalize_url(raw_url)
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM items WHERE url = ? OR url = ? LIMIT 1", (raw_url, norm_url))
        return cursor.fetchone() is not None

def insert_item(item: Dict[str, Any], db_path: str = DB_PATH) -> Optional[int]:
    """Inserts a new item if URL does not exist."""
    raw_url = item.get("url")
    if not raw_url or not isinstance(raw_url, str) or not raw_url.strip():
        return None
    url = raw_url.strip()
    norm_url = normalize_url(url)
    if item_exists(url, db_path) or item_exists(norm_url, db_path):
        return None
        
    with get_db_connection(db_path) as conn:
        with conn:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    INSERT INTO items (
                        source, category, title, url, summary, 
                        date_str, deadline, location, eligibility, draft_text, status, badge
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    item.get("source", "Unknown"),
                    item.get("category", "news"),
                    item.get("title", "").strip(),
                    norm_url,
                    item.get("summary", ""),
                    item.get("date_str", ""),
                    item.get("deadline", "TBA / Open"),
                    item.get("location", "Online"),
                    item.get("eligibility", "Open to all"),
                    item.get("draft_text", ""),
                    item.get("status", "pending"),
                    item.get("badge")
                ))
                return cursor.lastrowid
            except sqlite3.IntegrityError:
                return None

def get_item(item_id: int, db_path: str = DB_PATH) -> Optional[Dict[str, Any]]:
    """Retrieves a single item by ID."""
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM items WHERE id = ?", (item_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

def get_pending_items(limit: int = 50, db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    """Retrieves items awaiting admin review."""
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM items 
            WHERE status = 'pending' 
            ORDER BY created_at DESC 
            LIMIT ?
        """, (limit,))
        return [dict(row) for row in cursor.fetchall()]

def get_review_items(limit: int = 50, db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    """Retrieves items flagged for manual review with reason."""
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM items 
            WHERE status = 'review' 
            ORDER BY created_at DESC 
            LIMIT ?
        """, (limit,))
        return [dict(row) for row in cursor.fetchall()]

def update_item_status(item_id: int, status: str, db_path: str = DB_PATH) -> bool:
    """Updates status ('approved', 'rejected', 'posted')."""
    with get_db_connection(db_path) as conn:
        with conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE items SET status = ? WHERE id = ?", (status, item_id))
            return cursor.rowcount > 0

def update_item_draft(item_id: int, draft_text: str, db_path: str = DB_PATH) -> bool:
    """Updates the draft text of an item."""
    with get_db_connection(db_path) as conn:
        with conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE items SET draft_text = ? WHERE id = ?", (draft_text.strip(), item_id))
            return cursor.rowcount > 0

def get_stats(db_path: str = DB_PATH) -> Dict[str, int]:
    """Returns count of items by status."""
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT status, count(*) as count FROM items GROUP BY status")
        rows = cursor.fetchall()
        return {row["status"]: row["count"] for row in rows}

if __name__ == "__main__":
    init_db()
    print("Database initialized successfully at:", DB_PATH)
