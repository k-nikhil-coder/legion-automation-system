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
from typing import List, Dict, Optional, Any, Generator, Tuple

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

import config
from datetime import datetime, timedelta

def parse_date_ist(val: Any) -> Optional[datetime]:
    """Parses various date string formats to an Asia/Kolkata localized datetime."""
    if not val:
        return None
    s = str(val).strip()
    if not s or s.lower() in ("tba / open", "check website", "ongoing", "open", "tba", "check link"):
        return None
    res_dt = None
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
        "%d %b %Y",
        "%b %d, %Y",
        "%d %B %Y",
        "%B %d, %Y",
    ):
        try:
            dt = datetime.strptime(s, fmt)
            res_dt = dt.replace(tzinfo=config.IST)
            break
        except Exception:
            pass
    import re
    if not res_dt:
        # YYYY-MM-DD
        m = re.search(r'(\d{4})-(\d{1,2})-(\d{1,2})', s)
        if m:
            try:
                res_dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), 23, 59, 59, tzinfo=config.IST)
            except Exception:
                pass
    if not res_dt:
        # DD Mon YYYY
        m2 = re.search(r'(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\s+(\d{4})', s)
        if m2:
            try:
                day, mon, yr = int(m2.group(1)), m2.group(2)[:3], int(m2.group(3))
                dt = datetime.strptime(f"{day} {mon} {yr}", "%d %b %Y")
                res_dt = dt.replace(hour=23, minute=59, second=59, tzinfo=config.IST)
            except Exception:
                pass
    if not res_dt:
        # Mon DD, YYYY
        m3 = re.search(r'([A-Za-z]{3,9})\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})', s)
        if m3:
            try:
                mon, day, yr = m3.group(1)[:3], int(m3.group(2)), int(m3.group(3))
                dt = datetime.strptime(f"{day} {mon} {yr}", "%d %b %Y")
                res_dt = dt.replace(hour=23, minute=59, second=59, tzinfo=config.IST)
            except Exception:
                pass
    if not res_dt:
        # Mon YYYY
        m4 = re.search(r'([A-Za-z]{3,9})\s+(\d{4})', s)
        if m4:
            try:
                mon, yr = m4.group(1)[:3], int(m4.group(2))
                dt_first = datetime.strptime(f"1 {mon} {yr}", "%d %b %Y")
                res_dt = dt_first.replace(day=28, hour=23, minute=59, second=59, tzinfo=config.IST)
            except Exception:
                pass

    return res_dt

def compute_expires_at(item: Dict[str, Any], first_seen_dt: Optional[datetime] = None) -> Tuple[str, str]:
    """
    Computes expires_at and item status:
    a) If deadline or event_end/event_start exists and is in the future:
       expires_at = end of that day IST, capped at first_seen + MAX_FUTURE_DAYS.
    b) If that date is already past: expire immediately (status='expired').
    c) If no usable date: expires_at = first_seen + TTL_DAYS.
    NEVER defaults a missing date to now().
    """
    now = config.now_ist()
    if first_seen_dt is None:
        fs_val = item.get("first_seen") or item.get("created_at")
        if fs_val:
            parsed_fs = parse_date_ist(fs_val)
            first_seen_dt = parsed_fs if parsed_fs else now
        else:
            first_seen_dt = now

    usable_dt = None
    for field in ("deadline", "event_end", "event_start", "date_str"):
        val = item.get(field)
        if val:
            parsed = parse_date_ist(val)
            if parsed:
                usable_dt = parsed
                break

    if usable_dt is not None:
        end_of_day = usable_dt.replace(hour=23, minute=59, second=59)
        if end_of_day < now:
            return end_of_day.strftime("%Y-%m-%d %H:%M:%S"), "expired"
        else:
            max_cap = first_seen_dt + timedelta(days=config.MAX_FUTURE_DAYS)
            expires_dt = min(end_of_day, max_cap)
            status = item.get("status") or "pending"
            return expires_dt.strftime("%Y-%m-%d %H:%M:%S"), status
    else:
        expires_dt = first_seen_dt + timedelta(days=config.TTL_DAYS)
        if expires_dt < now:
            return expires_dt.strftime("%Y-%m-%d %H:%M:%S"), "expired"
        status = item.get("status") or "pending"
        return expires_dt.strftime("%Y-%m-%d %H:%M:%S"), status

def compute_fingerprint(title: str, start_date: str = "", city: str = "") -> str:
    """
    Generates dedup key: normalized(title) + start date + city.
    Collapses identical events across Unstop/Devpost/Dev.to.
    """
    import re
    clean_title = re.sub(r'[^a-z0-9]+', ' ', (title or "").lower()).strip()
    norm_date = ""
    if start_date:
        parsed = parse_date_ist(start_date)
        norm_date = parsed.strftime("%Y-%m-%d") if parsed else str(start_date).strip().lower()
    norm_city = config.normalize_city(city) if city else ""
    return f"{clean_title}|{norm_date}|{norm_city}"

def migrate_v3_schema(conn: sqlite3.Connection) -> None:
    """
    Executes ALTER TABLE ADD COLUMN for v3 schema expansion.
    Safely adds sub_category, experience_level, compensation, skill_tags,
    eligibility, perks, and relevance_score to opportunities and items tables.
    """
    cursor = conn.cursor()
    # Ensure opportunities table exists as well
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS opportunities (
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
            badge TEXT,
            first_seen TIMESTAMP,
            event_start TEXT,
            event_end TEXT,
            expires_at TIMESTAMP,
            city TEXT,
            mode TEXT,
            fingerprint TEXT,
            organizer TEXT,
            venue TEXT,
            fee TEXT,
            prize TEXT,
            apply_url TEXT,
            company TEXT,
            role TEXT,
            stipend TEXT,
            experience TEXT,
            enriched_data TEXT,
            sub_category TEXT DEFAULT '',
            experience_level TEXT DEFAULT 'ALL',
            compensation TEXT DEFAULT '',
            skill_tags TEXT DEFAULT '',
            perks TEXT DEFAULT '',
            relevance_score INTEGER DEFAULT 0
        );
    """)

    v3_cols = [
        ("sub_category", "TEXT DEFAULT ''"),
        ("experience_level", "TEXT DEFAULT 'ALL'"),
        ("compensation", "TEXT DEFAULT ''"),
        ("skill_tags", "TEXT DEFAULT ''"),
        ("eligibility", "TEXT DEFAULT ''"),
        ("perks", "TEXT DEFAULT ''"),
        ("relevance_score", "INTEGER DEFAULT 0"),
    ]

    for table_name in ("opportunities", "items"):
        for col_name, col_type in v3_cols:
            try:
                cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_type};")
            except sqlite3.OperationalError:
                pass


def init_db(db_path: str = DB_PATH):
    """Initializes the database schema and performs safe migrations for new columns."""
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

            # Safe column migration
            cursor.execute("PRAGMA table_info(items);")
            existing_cols = {row["name"] for row in cursor.fetchall()}
            
            columns_to_add = [
                ("first_seen", "TIMESTAMP"),
                ("event_start", "TEXT"),
                ("event_end", "TEXT"),
                ("deadline", "TEXT"),
                ("expires_at", "TIMESTAMP"),
                ("status", "TEXT DEFAULT 'pending'"),
                ("city", "TEXT"),
                ("mode", "TEXT"),
                ("fingerprint", "TEXT"),
                ("organizer", "TEXT"),
                ("venue", "TEXT"),
                ("fee", "TEXT"),
                ("prize", "TEXT"),
                ("apply_url", "TEXT"),
                ("company", "TEXT"),
                ("role", "TEXT"),
                ("stipend", "TEXT"),
                ("experience", "TEXT"),
                ("enriched_data", "TEXT"),
                ("badge", "TEXT")
            ]
            for col_name, col_type in columns_to_add:
                if col_name not in existing_cols:
                    try:
                        cursor.execute(f"ALTER TABLE items ADD COLUMN {col_name} {col_type};")
                    except Exception:
                        pass

            # Run v3 schema migration for opportunities and items
            migrate_v3_schema(conn)

            # Source Health Tracking Table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS source_health (
                    source_name TEXT PRIMARY KEY,
                    consecutive_zero_runs INTEGER DEFAULT 0,
                    last_run TIMESTAMP,
                    last_count INTEGER
                );
            """)

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_items_url ON items(url);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_items_status ON items(status);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_items_category ON items(category);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_items_fingerprint ON items(fingerprint);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_items_expires_at ON items(expires_at);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_items_relevance ON items(relevance_score);")

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

def fingerprint_exists(fingerprint: str, db_path: str = DB_PATH) -> bool:
    """Checks if an active item with the given fingerprint already exists."""
    if not fingerprint or fingerprint.strip() in ("", "||", "|", "||other"):
        return False
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM items WHERE fingerprint = ? LIMIT 1", (fingerprint,))
        return cursor.fetchone() is not None

def insert_item(item: Dict[str, Any], db_path: str = DB_PATH) -> Optional[int]:
    """Inserts a new item if URL and fingerprint do not already exist."""
    raw_url = item.get("url") or item.get("link")
    if not raw_url or not isinstance(raw_url, str) or not raw_url.strip():
        return None
    url = raw_url.strip()
    norm_url = normalize_url(url)
    if item_exists(url, db_path) or item_exists(norm_url, db_path):
        return None

    # Compute fingerprint
    fp = item.get("fingerprint") or compute_fingerprint(
        title=item.get("title", ""),
        start_date=item.get("event_start") or item.get("date_str") or item.get("deadline") or "",
        city=item.get("city") or item.get("location") or ""
    )
    if fp and fingerprint_exists(fp, db_path):
        return None

    # Determine first_seen
    first_seen_val = item.get("first_seen")
    first_seen_dt = None
    if first_seen_val:
        first_seen_dt = parse_date_ist(first_seen_val)
    if not first_seen_dt:
        first_seen_dt = config.now_ist()
    first_seen_str = first_seen_dt.strftime("%Y-%m-%d %H:%M:%S")

    # Compute expires_at and status
    expires_at_val = item.get("expires_at")
    status_val = item.get("status")
    computed_expires, computed_status = compute_expires_at(item, first_seen_dt=first_seen_dt)
    if not expires_at_val:
        expires_at_str = computed_expires
    else:
        expires_at_str = str(expires_at_val)

    if computed_status == "expired":
        status_val = "expired"
    elif not status_val or status_val in ("pending", "accepted"):
        status_val = computed_status or "pending"

    # City & Mode
    city_val = item.get("city") or config.normalize_city(item.get("location") or "")
    mode_val = item.get("mode") or ("ONLINE" if "online" in str(item.get("location", "")).lower() else "OFFLINE")

    with get_db_connection(db_path) as conn:
        with conn:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    INSERT INTO items (
                        source, category, title, url, summary, 
                        date_str, deadline, location, eligibility, draft_text, status, badge,
                        first_seen, event_start, event_end, expires_at, city, mode, fingerprint,
                        organizer, venue, fee, prize, apply_url, company, role, stipend, experience, enriched_data,
                        sub_category, experience_level, compensation, skill_tags, perks, relevance_score
                    ) VALUES (
                        ?, ?, ?, ?, ?, 
                        ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?
                    )
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
                    status_val,
                    item.get("badge"),
                    first_seen_str,
                    item.get("event_start", ""),
                    item.get("event_end", ""),
                    expires_at_str,
                    city_val,
                    mode_val,
                    fp,
                    item.get("organizer", ""),
                    item.get("venue", ""),
                    item.get("fee", ""),
                    item.get("prize", ""),
                    item.get("apply_url") or norm_url,
                    item.get("company", ""),
                    item.get("role", ""),
                    item.get("stipend", ""),
                    item.get("experience", ""),
                    item.get("enriched_data", ""),
                    item.get("sub_category", ""),
                    item.get("experience_level", "ALL"),
                    item.get("compensation", ""),
                    item.get("skill_tags", ""),
                    item.get("perks", ""),
                    int(item.get("relevance_score") or 0)
                ))
                row_id = cursor.lastrowid
                # Also keep opportunities table synchronized if present
                try:
                    cursor.execute("""
                        INSERT OR IGNORE INTO opportunities (
                            id, source, category, title, url, summary, date_str, deadline, location,
                            eligibility, draft_text, status, badge, first_seen, event_start, event_end,
                            expires_at, city, mode, fingerprint, organizer, venue, fee, prize, apply_url,
                            company, role, stipend, experience, enriched_data, sub_category,
                            experience_level, compensation, skill_tags, perks, relevance_score
                        ) VALUES (
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                        )
                    """, (
                        row_id,
                        item.get("source", "Unknown"), item.get("category", "news"),
                        item.get("title", "").strip(), norm_url, item.get("summary", ""),
                        item.get("date_str", ""), item.get("deadline", "TBA / Open"),
                        item.get("location", "Online"), item.get("eligibility", "Open to all"),
                        item.get("draft_text", ""), status_val, item.get("badge"),
                        first_seen_str, item.get("event_start", ""), item.get("event_end", ""),
                        expires_at_str, city_val, mode_val, fp, item.get("organizer", ""),
                        item.get("venue", ""), item.get("fee", ""), item.get("prize", ""),
                        item.get("apply_url") or norm_url, item.get("company", ""),
                        item.get("role", ""), item.get("stipend", ""), item.get("experience", ""),
                        item.get("enriched_data", ""), item.get("sub_category", ""),
                        item.get("experience_level", "ALL"), item.get("compensation", ""),
                        item.get("skill_tags", ""), item.get("perks", ""),
                        int(item.get("relevance_score") or 0)
                    ))
                except Exception:
                    pass
                return row_id
            except sqlite3.IntegrityError:
                return None


def insert_opportunity(item: Dict[str, Any], db_path: str = DB_PATH) -> Optional[int]:
    """Alias for insert_item to fulfill ASD-STE100 specification."""
    return insert_item(item, db_path=db_path)

def get_item(item_id: int, db_path: str = DB_PATH) -> Optional[Dict[str, Any]]:
    """Retrieves a single item by ID."""
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM items WHERE id = ?", (item_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

def get_pending_items(limit: int = 50, db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    """Retrieves active unexpired items awaiting admin review."""
    now_str = config.now_ist().strftime("%Y-%m-%d %H:%M:%S")
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM items 
            WHERE status IN ('pending', 'accepted')
              AND (expires_at IS NULL OR expires_at > ?)
            ORDER BY created_at DESC 
            LIMIT ?
        """, (now_str, limit))
        return [dict(row) for row in cursor.fetchall()]

def get_review_items(limit: int = 50, db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    """Retrieves items flagged for manual review with reason."""
    now_str = config.now_ist().strftime("%Y-%m-%d %H:%M:%S")
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM items 
            WHERE status = 'review'
              AND (expires_at IS NULL OR expires_at > ?)
            ORDER BY created_at DESC 
            LIMIT ?
        """, (now_str, limit))
        return [dict(row) for row in cursor.fetchall()]

def update_item_status(item_id: int, status: str, db_path: str = DB_PATH) -> bool:
    """Updates status ('approved', 'rejected', 'posted', 'expired')."""
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

def update_item(item_id: int, item_data: Dict[str, Any], db_path: str = DB_PATH) -> bool:
    """
    Updates an existing item's fields in SQLite.
    Never overwrites first_seen.
    Recomputes expires_at and status based on updated dates.
    """
    existing = get_item(item_id, db_path=db_path)
    if not existing:
        return False

    first_seen_val = existing.get("first_seen") or existing.get("created_at")
    first_seen_dt = parse_date_ist(first_seen_val) or config.now_ist()

    # Recompute expires_at and status
    expires_at_str, computed_status = compute_expires_at(item_data, first_seen_dt=first_seen_dt)
    status_val = item_data.get("status") or existing.get("status") or "pending"
    if computed_status == "expired":
        status_val = "expired"

    # Recompute fingerprint if title/date/city changed
    fp = item_data.get("fingerprint") or compute_fingerprint(
        title=item_data.get("title") or existing.get("title", ""),
        start_date=item_data.get("event_start") or item_data.get("deadline") or existing.get("deadline", ""),
        city=item_data.get("city") or existing.get("city", "")
    )

    with get_db_connection(db_path) as conn:
        with conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE items SET
                    title = COALESCE(?, title),
                    summary = COALESCE(?, summary),
                    deadline = COALESCE(?, deadline),
                    event_start = COALESCE(?, event_start),
                    event_end = COALESCE(?, event_end),
                    location = COALESCE(?, location),
                    city = COALESCE(?, city),
                    mode = COALESCE(?, mode),
                    venue = COALESCE(?, venue),
                    fee = COALESCE(?, fee),
                    prize = COALESCE(?, prize),
                    eligibility = COALESCE(?, eligibility),
                    company = COALESCE(?, company),
                    role = COALESCE(?, role),
                    stipend = COALESCE(?, stipend),
                    experience = COALESCE(?, experience),
                    sub_category = COALESCE(?, sub_category),
                    experience_level = COALESCE(?, experience_level),
                    compensation = COALESCE(?, compensation),
                    skill_tags = COALESCE(?, skill_tags),
                    perks = COALESCE(?, perks),
                    relevance_score = COALESCE(?, relevance_score),
                    expires_at = ?,
                    status = ?,
                    fingerprint = ?
                WHERE id = ?
            """, (
                item_data.get("title"),
                item_data.get("summary"),
                item_data.get("deadline"),
                item_data.get("event_start"),
                item_data.get("event_end"),
                item_data.get("location"),
                item_data.get("city"),
                item_data.get("mode"),
                item_data.get("venue"),
                item_data.get("fee"),
                item_data.get("prize"),
                item_data.get("eligibility"),
                item_data.get("company"),
                item_data.get("role"),
                item_data.get("stipend"),
                item_data.get("experience"),
                item_data.get("sub_category"),
                item_data.get("experience_level"),
                item_data.get("compensation"),
                item_data.get("skill_tags"),
                item_data.get("perks"),
                item_data.get("relevance_score"),
                expires_at_str,
                status_val,
                fp,
                item_id
            ))
            return cursor.rowcount > 0

def get_stats(db_path: str = DB_PATH) -> Dict[str, int]:
    """Returns count of items by status."""
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT status, count(*) as count FROM items GROUP BY status")
        rows = cursor.fetchall()
        return {row["status"]: row["count"] for row in rows}

def expire_old_items(db_path: str = DB_PATH) -> int:
    """
    Expiry job: UPDATE status='expired' WHERE expires_at < now.
    Does NOT delete rows so that URLs remain for deduplication.
    """
    now_str = config.now_ist().strftime("%Y-%m-%d %H:%M:%S")
    with get_db_connection(db_path) as conn:
        with conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE items 
                SET status = 'expired' 
                WHERE expires_at IS NOT NULL 
                  AND expires_at < ? 
                  AND status != 'expired'
            """, (now_str,))
            return cursor.rowcount

def purge_old_expired_bodies(db_path: str = DB_PATH) -> int:
    """
    After PURGE_BODY_DAYS, null out raw body/summary and draft_text of expired rows
    but keep url, title, fingerprint.
    """
    cutoff = (config.now_ist() - timedelta(days=config.PURGE_BODY_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    with get_db_connection(db_path) as conn:
        with conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE items 
                SET summary = NULL, draft_text = NULL 
                WHERE status = 'expired' 
                  AND (expires_at < ? OR (expires_at IS NULL AND first_seen < ?))
                  AND (summary IS NOT NULL OR draft_text IS NOT NULL)
            """, (cutoff, cutoff))
            return cursor.rowcount

def update_source_health(source_name: str, item_count: int, db_path: str = DB_PATH) -> int:
    """
    Updates consecutive 0-item run counts for a source.
    Returns the current consecutive_zero_runs.
    """
    now_str = config.now_ist().strftime("%Y-%m-%d %H:%M:%S")
    with get_db_connection(db_path) as conn:
        with conn:
            cursor = conn.cursor()
            cursor.execute("SELECT consecutive_zero_runs FROM source_health WHERE source_name = ?", (source_name,))
            row = cursor.fetchone()
            if row is None:
                zero_runs = 1 if item_count == 0 else 0
                cursor.execute("""
                    INSERT INTO source_health (source_name, consecutive_zero_runs, last_run, last_count)
                    VALUES (?, ?, ?, ?)
                """, (source_name, zero_runs, now_str, item_count))
                return zero_runs
            else:
                prev_zeros = row["consecutive_zero_runs"]
                new_zeros = (prev_zeros + 1) if item_count == 0 else 0
                cursor.execute("""
                    UPDATE source_health 
                    SET consecutive_zero_runs = ?, last_run = ?, last_count = ?
                    WHERE source_name = ?
                """, (new_zeros, now_str, item_count, source_name))
                return new_zeros

if __name__ == "__main__":
    init_db()
    print("Database initialized successfully at:", DB_PATH)
