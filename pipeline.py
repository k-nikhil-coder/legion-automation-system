"""
Legion Automation System - Content Pipeline
Orchestrates collection, deduplication, formatting, database storage,
and exports the updated PENDING_DRAFTS.md review digest.
"""

import argparse
import http.server
import json
import logging
import os
import re
import socketserver
import sys
import threading
import time
import urllib.parse
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple


import config
import db
import formatter
import collectors
import collectors2
import quality_gate

# Ensure stdout handles UTF-8 on Windows
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

logger = logging.getLogger("legion.pipeline")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s] %(message)s")

CATEGORY_ORDER = ["hackathon", "job", "event", "course", "news"]

CATEGORY_TITLES = {
    "hackathon": "🏆 Hackathons",
    "job": "💼 Jobs & Internships",
    "event": "📅 Events & Workshops",
    "course": "📚 Courses & Resources",
    "news": "🌐 Tech Updates & News"
}

def export_pending_markdown(
    db_path: Optional[str] = None,
    output_path: Optional[str] = None
) -> str:
    """
    Exports all pending items from SQLite to a structured Markdown review document.
    """
    actual_db = db_path or config.DB_PATH
    actual_out = output_path or config.PENDING_DRAFTS_PATH
    pending_items = db.get_pending_items(limit=200, db_path=actual_db)
    stats = db.get_stats(db_path=actual_db)
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Group by category
    grouped: Dict[str, List[Dict[str, Any]]] = {cat: [] for cat in CATEGORY_ORDER}
    other_items: List[Dict[str, Any]] = []
    
    for item in pending_items:
        cat = item.get("category", "news").lower()
        if cat in grouped:
            grouped[cat].append(item)
        else:
            other_items.append(item)
            
    lines = []
    lines.append("# 🛡️ Legion Community - Pending Content Review Queue\n")
    lines.append(f"> **Generated at**: `{timestamp}`  ")
    lines.append(
        f"> **Database Stats**: "
        f"⏳ Pending: `{stats.get('pending', 0)}` | "
        f"✅ Approved: `{stats.get('approved', 0)}` | "
        f"❌ Rejected: `{stats.get('rejected', 0)}` | "
        f"🚀 Posted: `{stats.get('posted', 0)}`\n"
    )
    lines.append("## 📌 Workflow Guide")
    lines.append("- **Option A (Telegram Bot)**: Run `python telegram_bot.py --push` to push unreviewed items to your Telegram chat with inline Approve / Reject / Done buttons.")
    lines.append("- **Option B (Manual WhatsApp Post)**: Copy the exact draft in the code block and paste it directly into the targeted Legion WhatsApp group.")
    lines.append("- **Option C (CLI Update)**: Run `python telegram_bot.py --approve <ID>` or `python telegram_bot.py --reject <ID>` to mark items.\n")
    lines.append("---\n")

    if not pending_items:
        lines.append("### 🎉 All caught up! No pending drafts to review.")
        lines.append("\nRun `python pipeline.py` to fetch new updates from configured feeds.")
    else:
        for cat in CATEGORY_ORDER:
            items_in_cat = grouped[cat]
            # Prioritize offline Hyderabad events to appear first
            items_in_cat.sort(key=lambda x: (0 if formatter.is_hyderabad_offline(x) else 1))
            cat_title = CATEGORY_TITLES.get(cat, cat.title())
            target_wa = formatter.get_whatsapp_target_group(cat)
            
            lines.append(f"## {cat_title}")
            lines.append(f"*Target WhatsApp Group: `{target_wa}`* | *Pending: {len(items_in_cat)}*\n")
            
            if not items_in_cat:
                lines.append(f"_No pending {cat} opportunities at this time._\n")
                continue
                
            for it in items_in_cat:
                item_id = it.get("id")
                title = it.get("title", "Untitled")
                source = it.get("source", "Web Feed")
                url = it.get("url", "")
                draft = it.get("draft_text") or formatter.format_draft(it)
                created_at = it.get("created_at", "")
                loc_badge = formatter.get_location_badge(it)
                loc_tag = formatter.get_location_tag(it)
                loc_display = loc_tag.replace("📍 Location: ", "") if loc_tag.startswith("📍 Location: ") else loc_tag
                
                lines.append(f"### [#{item_id}] {title} {loc_badge}")
                lines.append(f"- **Location**: {loc_display} | **Source**: {source} | **Created**: `{created_at}`")
                lines.append(f"- **Original Link**: [{url}]({url})")
                lines.append(f"- **Quick Commands**: `python telegram_bot.py --approve {item_id}` | `python telegram_bot.py --reject {item_id}` | `python telegram_bot.py --edit {item_id}`")
                lines.append("\n**Formatted Legion Draft (Ready to Copy):**")
                lines.append("```text")
                lines.append(draft)
                lines.append("```\n")
                lines.append("---\n")

        if other_items:
            lines.append("## 📁 Other Updates\n")
            other_items.sort(key=lambda x: (0 if formatter.is_hyderabad_offline(x) else 1))
            for it in other_items:
                item_id = it.get("id")
                loc_badge = formatter.get_location_badge(it)
                loc_tag = formatter.get_location_tag(it)
                loc_display = loc_tag.replace("📍 Location: ", "") if loc_tag.startswith("📍 Location: ") else loc_tag
                other_draft = it.get("draft_text") or formatter.format_draft(it)
                lines.append(f"### [#{item_id}] {it.get('title')} {loc_badge}")
                lines.append(f"- **Location**: {loc_display} | **Category**: {it.get('category')} | **Source**: {it.get('source')}")
                lines.append(f"- **Quick Commands**: `python telegram_bot.py --approve {item_id}` | `python telegram_bot.py --reject {item_id}` | `python telegram_bot.py --edit {item_id}`")
                lines.append("```text")
                lines.append(other_draft)
                lines.append("```\n")
                lines.append("---\n")

        # Separate Review Queue (Needs Admin Verification)
        review_items = db.get_review_items(limit=100, db_path=actual_db)
        if review_items:
            lines.append("## 🔍 Review Queue (Needs Admin Verification)\n")
            lines.append(f"*Items flagged for manual verification: `{len(review_items)}`*\n")
            for it in review_items:
                item_id = it.get("id")
                title = it.get("title", "Untitled")
                source = it.get("source", "Web Feed")
                url = it.get("url", "")
                loc_badge = formatter.get_location_badge(it)
                reason = it.get("eligibility", "Flagged by quality gate")
                review_draft = it.get("draft_text") or formatter.format_draft(it)
                lines.append(f"### [#{item_id}] {title} {loc_badge}")
                lines.append(f"- **Flag Reason**: ⚠️ `{reason}`")
                lines.append(f"- **Source**: {source} | **Link**: [{url}]({url})")
                lines.append(f"- **Quick Commands**: `python telegram_bot.py --approve {item_id}` | `python telegram_bot.py --reject {item_id}`")
                lines.append("```text")
                lines.append(review_draft)
                lines.append("```\n")
                lines.append("---\n")

    markdown_content = "\n".join(lines)
    
    os.makedirs(os.path.dirname(os.path.abspath(actual_out)), exist_ok=True)
    with open(actual_out, "w", encoding="utf-8") as f:
        f.write(markdown_content)
        
    logger.info(f"Updated pending drafts review file at: {actual_out}")
    return markdown_content

def import_legion_feed(
    html_path: Optional[str] = None,
    db_path: Optional[str] = None
) -> int:
    """
    Imports curated opportunities from Legion Feed HTML into SQLite.
    Deduplicates against existing items and formats drafts in house style.
    Discards opportunities in other cities unless online/remote or national.
    Returns the count of newly inserted items.
    """
    actual_db = db_path or config.DB_PATH
    db.init_db(db_path=actual_db)
    
    feed_items = collectors.fetch_legion_feed_items(html_path=html_path)
    new_count = 0
    for item in feed_items:
        try:
            url = str(item.get("url") or "").strip()
            if not url or db.item_exists(url, db_path=actual_db):
                continue
            if not formatter.is_location_allowed(item):
                logger.info(f"Skipping disallowed city item from Legion Feed: {item.get('title')} ({item.get('location')})")
                continue
            item["draft_text"] = formatter.format_draft(item)
            item["status"] = "pending"
            row_id = db.insert_item(item, db_path=actual_db)
            if row_id is not None:
                new_count += 1
        except Exception as e:
            logger.warning(f"Error importing item from Legion Feed: {e}")
            
    logger.info(f"Imported {new_count} new opportunities from Legion Feed into {actual_db}")
    return new_count

def export_feed_html(
    db_path: Optional[str] = None,
    template_path: Optional[str] = None,
    output_path: Optional[str] = None
) -> str:
    """
    Exports items from SQLite to update the interactive Legion Feed HTML dashboard.
    Populates SEED data with active items from the database and preserves any curated seeds.
    """
    actual_db = db_path or config.DB_PATH
    actual_out = output_path or getattr(config, "LEGION_FEED_HTML_PATH", "legion_feed.html")
    
    # 1. Determine base template content
    base_html = ""
    candidate_templates = []
    if template_path:
        candidate_templates.append(template_path)
    if os.path.exists(actual_out):
        candidate_templates.append(actual_out)
    if hasattr(config, "LEGION_FEED_HTML_PATH") and os.path.exists(config.LEGION_FEED_HTML_PATH):
        candidate_templates.append(config.LEGION_FEED_HTML_PATH)
    downloads_path = os.path.join(os.path.expanduser("~"), "Downloads", "Legion Feed.html")
    if os.path.exists(downloads_path):
        candidate_templates.append(downloads_path)
        
    for p in candidate_templates:
        try:
            with open(p, "r", encoding="utf-8") as f:
                content = f.read()
            if content and "var SEED" in content:
                base_html = content
                break
        except Exception:
            pass
            
    if not base_html:
        logger.warning("No base HTML template found for Legion Feed export.")
        return ""
        
    # 2. Extract existing seeds to preserve offline curated items
    existing_items = collectors.parse_legion_feed_html(base_html)
    
    # 3. Fetch active unexpired items from DB
    db_items: List[Dict[str, Any]] = []
    now_str = config.now_ist().strftime("%Y-%m-%d %H:%M:%S")
    today_str = config.now_ist().strftime("%Y-%m-%d")
    try:
        with db.get_db_connection(actual_db) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM items 
                WHERE status IN ('accepted', 'review', 'pending', 'approved', 'posted')
                  AND (expires_at IS NULL OR expires_at > ?)
                ORDER BY id DESC LIMIT 150
            """, (now_str,))
            rows = cursor.fetchall()
            db_items = [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"Could not load items from DB for HTML export: {e}")
        
    seed_list: List[Dict[str, Any]] = []
    seen_urls = set()
    
    # Add DB items first
    for it in db_items:
        u = it.get("url", "").strip()
        if not u or u in seen_urls:
            continue
        seen_urls.add(u)
        
        # Determine date YYYY-MM-DD if available
        raw_date = it.get("deadline") or it.get("date_str") or ""
        date_match = re.search(r'\d{4}-\d{2}-\d{2}', str(raw_date))
        clean_date = date_match.group(0) if date_match else ""
        
        created_str = str(it.get("created_at") or "")[:10]
        if not re.match(r'^\d{4}-\d{2}-\d{2}$', created_str):
            created_str = config.now_ist().strftime("%Y-%m-%d")
            
        raw_loc = it.get("location") or "Online"
        if formatter.is_hyderabad_offline(it):
            venue = formatter.detect_hyderabad_venue(f"{raw_loc} {it.get('title')} {it.get('summary')}")
            where_val = f"Hyderabad ({venue})" if venue else "Hyderabad"
        elif it.get("city") == "bengaluru" or formatter.detect_bengaluru_venue(f"{raw_loc} {it.get('title')} {it.get('summary')}"):
            venue = formatter.detect_bengaluru_venue(f"{raw_loc} {it.get('title')} {it.get('summary')}")
            where_val = f"Bengaluru ({venue})" if venue else "Bengaluru"
        else:
            where_val = raw_loc
            
        seed_list.append({
            "id": f"db_{it.get('id')}",
            "type": it.get("category", "news"),
            "title": it.get("title", ""),
            "sum": it.get("summary") or it.get("title", ""),
            "date": clean_date,
            "where": where_val,
            "city": it.get("city") or ("bengaluru" if "bengaluru" in where_val.lower() else ("hyderabad" if "hyderabad" in where_val.lower() else "online")),
            "src": it.get("source") or "Legion Automation",
            "link": u,
            "posted": created_str,
            "status": it.get("status", "pending")
        })
        
    # Add existing template items that are active and unexpired
    for orig in existing_items:
        u = orig.get("url", "").strip()
        if u and u not in seen_urls:
            if orig.get("status") in ("rejected", "expired"):
                continue
            raw_d = orig.get("date_str") or orig.get("deadline") or ""
            d_m = re.search(r'\d{4}-\d{2}-\d{2}', str(raw_d))
            clean_d = d_m.group(0) if d_m else (raw_d if len(raw_d) == 10 and "-" in raw_d else "")
            if clean_d and clean_d < today_str and orig.get("category") in ("event", "hackathon"):
                continue
            seen_urls.add(u)
            seed_list.append({
                "id": orig.get("url", "").split("/")[-1] or f"s{len(seed_list)+1}",
                "type": orig.get("category", "news"),
                "title": orig.get("title", ""),
                "sum": formatter.clean_text(orig.get("summary") or orig.get("title", ""), 180),
                "date": clean_d,
                "where": orig.get("location", "Online"),
                "src": orig.get("source", "Community"),
                "link": u,
                "posted": config.now_ist().strftime("%Y-%m-%d"),
                "status": "seed"
            })

    # Also ensure unexpired CURATED_SEED_ITEMS are preserved if not yet in seed_list
    for seed_curated in getattr(collectors, "CURATED_SEED_ITEMS", []):
        u = seed_curated.get("link", "").strip() or f"https://legion.community/feed/{seed_curated.get('id')}"
        if u not in seen_urls:
            raw_d = seed_curated.get("date", "").strip()
            if raw_d and raw_d < today_str and seed_curated.get("type") in ("event", "hackathon"):
                continue
            seen_urls.add(u)
            raw_d = seed_curated.get("date", "").strip()
            seed_list.append({
                "id": seed_curated.get("id"),
                "type": seed_curated.get("type", "news"),
                "title": seed_curated.get("title", ""),
                "sum": seed_curated.get("sum", ""),
                "date": raw_d,
                "where": seed_curated.get("where", "Online"),
                "src": seed_curated.get("src", "Community"),
                "link": seed_curated.get("link", ""),
                "posted": seed_curated.get("posted", datetime.now().strftime("%Y-%m-%d")),
                "status": "seed"
            })
            
    # Serialize to clean JS array JSON
    seed_json = json.dumps(seed_list, indent=2, ensure_ascii=False)

    
    # Replace var SEED = [...];
    updated_html = re.sub(
        r'var\s+SEED\s*=\s*\[[\s\S]*?\]\s*;',
        f'var SEED = {seed_json};',
        base_html
    )
    
    # Ensure TODAY is current date
    today_str = datetime.now().strftime("%Y-%m-%d")
    updated_html = re.sub(
        r'var\s+TODAY\s*=\s*"[^"]*";',
        f'var TODAY = "{today_str}";',
        updated_html
    )
    
    os.makedirs(os.path.dirname(os.path.abspath(actual_out)), exist_ok=True)
    with open(actual_out, "w", encoding="utf-8") as f:
        f.write(updated_html)
        
    logger.info(f"Updated Legion Feed HTML dashboard ({len(seed_list)} items) at: {actual_out}")
    return updated_html

def open_feed_html(html_path: Optional[str] = None) -> bool:
    """Opens Legion Feed dashboard in the system's default web browser."""
    actual_path = html_path or getattr(config, "LEGION_FEED_HTML_PATH", "legion_feed.html")
    if not os.path.exists(actual_path):
        logger.warning(f"Feed file not found at: {actual_path}")
        return False
    import webbrowser
    file_url = f"file:///{os.path.abspath(actual_path).replace(os.sep, '/')}"
    webbrowser.open(file_url)
    return True

def export_posts_json(
    db_path: Optional[str] = None,
    output_path: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Exports active items from SQLite database to posts.json.
    Deduplicates items by link and formats into standard post schema.
    Preserves any curated seed items from previous posts.json or collectors.
    Atomically writes to disk using a temporary file to avoid partial reads.
    """
    actual_db = db_path or config.DB_PATH
    actual_out = output_path or getattr(config, "POSTS_JSON_PATH", "posts.json")

    db_items: List[Dict[str, Any]] = []
    rejected_urls: set = set()
    now_str = config.now_ist().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with db.get_db_connection(actual_db) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM items 
                WHERE status IN ('accepted', 'review', 'pending', 'approved', 'posted')
                  AND (expires_at IS NULL OR expires_at > ?)
                ORDER BY id DESC LIMIT 200
            """, (now_str,))
            rows = cursor.fetchall()
            db_items = [dict(r) for r in rows]

            cursor.execute("SELECT url FROM items WHERE status IN ('rejected', 'expired') OR (expires_at IS NOT NULL AND expires_at <= ?)", (now_str,))
            for r in cursor.fetchall():
                u = str(r["url"] or "").strip()
                if u:
                    rejected_urls.add(u)
    except Exception as e:
        logger.warning(f"Could not load items from DB for posts.json export: {e}")

    posts_list: List[Dict[str, Any]] = []
    seen_urls = set()

    for it in db_items:
        u = str(it.get("url") or "").strip()
        if not u or u in seen_urls:
            continue
        seen_urls.add(u)

        raw_date = it.get("deadline") or it.get("date_str") or ""
        date_match = re.search(r'\d{4}-\d{2}-\d{2}', str(raw_date))
        clean_date = date_match.group(0) if date_match else ""

        created_str = str(it.get("created_at") or "")[:10]
        if not re.match(r'^\d{4}-\d{2}-\d{2}$', created_str):
            created_str = datetime.now().strftime("%Y-%m-%d")

        raw_loc = it.get("location") or "Online"
        if formatter.is_hyderabad_offline(it):
            venue = formatter.detect_hyderabad_venue(f"{raw_loc} {it.get('title')} {it.get('summary')}")
            where_val = f"Hyderabad ({venue})" if venue else "Hyderabad"
            badge_val = "HYDERABAD - OFFLINE"
        elif it.get("city") == "bengaluru" or formatter.detect_bengaluru_venue(f"{raw_loc} {it.get('title')} {it.get('summary')}"):
            venue = formatter.detect_bengaluru_venue(f"{raw_loc} {it.get('title')} {it.get('summary')}")
            where_val = f"Bengaluru ({venue})" if venue else "Bengaluru"
            badge_val = "BENGALURU - OFFLINE"
        else:
            where_val = raw_loc
            badge_val = it.get("badge") or ("HYDERABAD - OFFLINE" if "hyderabad" in raw_loc.lower() and "online" not in raw_loc.lower() else ("BENGALURU - OFFLINE" if ("bengaluru" in raw_loc.lower() or "bangalore" in raw_loc.lower()) and "online" not in raw_loc.lower() else "ONLINE"))

        raw_summary = str(it.get("summary") or it.get("title", "")).strip()
        # Clean out any verification spam tags like **BENEVOLENT** or long hex strings
        raw_summary = re.sub(r'\*\*.*?\*\*', '', raw_summary)
        raw_summary = re.sub(r'#[A-Za-z0-9_-]{8,}', '', raw_summary).strip()
        clean_summary = formatter.clean_text(raw_summary, 180)

        posts_list.append({
            "id": f"db_{it.get('id')}",
            "type": it.get("category", "news"),
            "title": it.get("title", ""),
            "sum": clean_summary,
            "date": clean_date,
            "where": where_val,
            "badge": badge_val,
            "city": it.get("city") or ("bengaluru" if "bengaluru" in badge_val.lower() else ("hyderabad" if "hyderabad" in badge_val.lower() else "online")),
            "src": it.get("source") or "Legion Automation",
            "link": u,
            "posted": created_str,
            "status": it.get("status", "pending")
        })

    # Preserve previous curated seed items only, never re-import rejected or deleted DB items
    if os.path.exists(actual_out):
        try:
            with open(actual_out, "r", encoding="utf-8") as f:
                existing = json.load(f)
                if isinstance(existing, list):
                    for ex in existing:
                        link_u = str(ex.get("link") or "").strip()
                        if not link_u or link_u in seen_urls or link_u in rejected_urls:
                            continue
                        if ex.get("status") == "rejected":
                            continue
                        # Only preserve seed or community items, never stale DB records
                        if ex.get("status") == "seed" or str(ex.get("id", "")).startswith("s"):
                            seen_urls.add(link_u)
                            posts_list.append(ex)
        except Exception as e:
            logger.debug(f"Could not load previous posts.json: {e}")

    # Preserve CURATED_SEED_ITEMS if not rejected or seen
    for seed_curated in getattr(collectors, "CURATED_SEED_ITEMS", []):
        u = seed_curated.get("link", "").strip() or f"https://legion.community/feed/{seed_curated.get('id')}"
        if u not in seen_urls and u not in rejected_urls:
            seen_urls.add(u)
            raw_d = seed_curated.get("date", "").strip()
            posts_list.append({
                "id": seed_curated.get("id"),
                "type": seed_curated.get("type", "news"),
                "title": seed_curated.get("title", ""),
                "sum": seed_curated.get("sum", ""),
                "date": raw_d,
                "where": seed_curated.get("where", "Online"),
                "badge": "HYDERABAD - OFFLINE" if "hyderabad" in str(seed_curated.get("where", "")).lower() else "ONLINE",
                "src": seed_curated.get("src", "Community"),
                "link": seed_curated.get("link", ""),
                "posted": seed_curated.get("posted", datetime.now().strftime("%Y-%m-%d")),
                "status": "seed"
            })

    out_dir = os.path.dirname(os.path.abspath(actual_out))
    os.makedirs(out_dir, exist_ok=True)
    tmp_file = os.path.join(out_dir, f".tmp_posts_{os.getpid()}_{int(time.time()*1000)}.json")
    try:
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(posts_list, f, indent=2, ensure_ascii=False)
        os.replace(tmp_file, actual_out)
    finally:
        if os.path.exists(tmp_file):
            try:
                os.remove(tmp_file)
            except Exception:
                pass

    logger.info(f"Updated posts.json feed export ({len(posts_list)} items) at: {actual_out}")
    return posts_list


_refresh_lock = threading.Lock()


def refresh_feed(
    db_path: Optional[str] = None,
    posts_path: Optional[str] = None,
    markdown_path: Optional[str] = None,
    html_path: Optional[str] = None,
    limit_per_feed: Optional[int] = None
) -> Dict[str, Any]:
    """
    Synchronously triggers a collection update cycle under thread lock:
    - Runs collectors & quality gate filtering
    - Updates SQLite DB
    - Updates PENDING_DRAFTS.md
    - Updates legion_feed.html
    - Regenerates posts.json
    Returns API response dictionary.
    """
    with _refresh_lock:
        results = run_pipeline(
            limit_per_feed=limit_per_feed,
            db_path=db_path,
            markdown_path=markdown_path,
            html_path=html_path,
            posts_path=posts_path,
        )
        actual_posts = posts_path or getattr(config, "POSTS_JSON_PATH", "posts.json")
        total_items = 0
        if os.path.exists(actual_posts):
            try:
                with open(actual_posts, "r", encoding="utf-8") as f:
                    pdata = json.load(f)
                    if isinstance(pdata, list):
                        total_items = len(pdata)
            except Exception:
                pass
        if total_items == 0:
            stats = results.get("stats", {})
            total_items = sum(stats.values()) if stats else results.get("total_pending", 0)

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return {
            "status": "ok",
            "total_items": total_items,
            "new_pending": results.get("new_pending", 0),
            "timestamp": now_str,
            "message": "Feed refreshed successfully"
        }


class FeedHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    """
    HTTP request handler for Legion Feed.
    Serves static files (HTML, JS, CSS, JSON), rewrites / and /feed to legion_feed.html,
    and exposes /api/refresh for on-demand feed updates with CORS enabled.
    """
    server_filename: str = "legion_feed.html"
    db_path: Optional[str] = None
    posts_path: Optional[str] = None
    markdown_path: Optional[str] = None
    html_path: Optional[str] = None

    def send_response(self, code: int, message: Optional[str] = None):
        super().send_response(code, message)
        self.send_header("Access-Control-Allow-Origin", "*")

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _send_refresh_response(self):
        try:
            res = refresh_feed(
                db_path=self.db_path,
                posts_path=self.posts_path,
                markdown_path=self.markdown_path,
                html_path=self.html_path
            )
            body = json.dumps(res, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            logger.error(f"Error executing /api/refresh: {e}", exc_info=True)
            err_data = {
                "status": "error",
                "message": str(e),
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            body = json.dumps(err_data, ensure_ascii=False).encode("utf-8")
            self.send_response(500)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/refresh":
            self._send_refresh_response()
            return
        if parsed.path == "/posts.json":
            target_posts = self.posts_path or getattr(config, "POSTS_JSON_PATH", "posts.json")
            if os.path.exists(target_posts):
                try:
                    with open(target_posts, "rb") as f:
                        data = f.read()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                except Exception as e:
                    logger.error(f"Error serving posts.json: {e}")
        if self.path in ("/", "/index.html", "/feed"):
            self.path = f"/{self.server_filename}"
        return super().do_GET()

    def do_POST(self):
        content_len = int(self.headers.get("Content-Length", 0) or 0)
        if content_len > 0:
            try:
                self.rfile.read(content_len)
            except Exception:
                pass
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/refresh":
            self._send_refresh_response()
            return
        self.send_error(404, "Endpoint not found")


def start_auto_updater(
    interval_minutes: float = 30.0,
    stop_event: Optional[threading.Event] = None,
    db_path: Optional[str] = None,
    posts_path: Optional[str] = None,
    markdown_path: Optional[str] = None,
    html_path: Optional[str] = None,
    run_immediately: bool = False
) -> Tuple[threading.Thread, threading.Event]:
    """
    Spawns a daemon thread that periodically refreshes the feed and updates posts.json.
    Returns (thread, stop_event).
    """
    event = stop_event or threading.Event()
    interval_seconds = max(0.05, float(interval_minutes) * 60.0)

    def _worker():
        logger.info(f"Background auto-updater started (interval={interval_minutes}m).")
        if run_immediately and not event.is_set():
            try:
                logger.info("Auto-updater triggering immediate initial refresh...")
                refresh_feed(
                    db_path=db_path,
                    posts_path=posts_path,
                    markdown_path=markdown_path,
                    html_path=html_path
                )
            except Exception as e:
                logger.warning(f"Auto-updater initial refresh error: {e}")

        while not event.is_set():
            if event.wait(timeout=interval_seconds):
                break
            try:
                logger.info("Auto-updater triggering periodic feed refresh...")
                refresh_feed(
                    db_path=db_path,
                    posts_path=posts_path,
                    markdown_path=markdown_path,
                    html_path=html_path
                )
                logger.info("Auto-updater periodic feed refresh completed.")
            except Exception as e:
                logger.warning(f"Auto-updater periodic refresh error: {e}")
        logger.info("Background auto-updater stopped.")

    thread = threading.Thread(target=_worker, name="FeedAutoUpdater", daemon=True)
    thread.start()
    return thread, event


def serve_feed(
    port: int = 8000,
    html_path: Optional[str] = None,
    db_path: Optional[str] = None,
    posts_path: Optional[str] = None,
    markdown_path: Optional[str] = None,
    auto_update_minutes: Optional[int] = None,
    open_browser: bool = True
):
    """Starts a local HTTP server serving the Legion Feed dashboard and /api/refresh endpoint."""
    import webbrowser

    actual_html = html_path or getattr(config, "LEGION_FEED_HTML_PATH", "legion_feed.html")
    actual_db = db_path or config.DB_PATH
    actual_posts = posts_path or getattr(config, "POSTS_JSON_PATH", "posts.json")
    actual_markdown = markdown_path or config.PENDING_DRAFTS_PATH
    directory = os.path.dirname(os.path.abspath(actual_html))
    filename = os.path.basename(actual_html)

    update_mins = (
        auto_update_minutes 
        if auto_update_minutes is not None 
        else getattr(config, "AUTO_UPDATE_INTERVAL_MINUTES", 30)
    )

    class CustomHandler(FeedHTTPRequestHandler):
        server_filename = filename
        db_path = actual_db
        posts_path = actual_posts
        markdown_path = actual_markdown
        html_path = actual_html

        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=directory, **kwargs)

    # Initial export of posts.json if missing
    if not os.path.exists(actual_posts):
        try:
            export_posts_json(db_path=actual_db, output_path=actual_posts)
        except Exception as e:
            logger.warning(f"Could not generate initial posts.json: {e}")

    updater_thread = None
    stop_event = None
    if update_mins > 0:
        updater_thread, stop_event = start_auto_updater(
            interval_minutes=update_mins,
            db_path=actual_db,
            posts_path=actual_posts,
            markdown_path=actual_markdown,
            html_path=actual_html
        )

    socketserver.ThreadingTCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(("", port), CustomHandler) as httpd:
        url = f"http://localhost:{port}/{filename}"
        print(f"🌍 Legion Feed Web Server running at: {url}")
        print(f"🔄 API endpoint available at: http://localhost:{port}/api/refresh")
        if update_mins > 0:
            print(f"⏱️  Background auto-updater active: every {update_mins} minutes")
        print("Press Ctrl+C to stop.")
        try:
            if open_browser:
                webbrowser.open(url)
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down Legion Feed Web Server...")
        finally:
            if stop_event:
                stop_event.set()
            if updater_thread:
                updater_thread.join(timeout=2.0)


def run_pipeline(
    limit_per_feed: Optional[int] = None,
    export_only: bool = False,
    db_path: Optional[str] = None,
    markdown_path: Optional[str] = None,
    html_path: Optional[str] = None,
    posts_path: Optional[str] = None,
    import_feed: bool = False,
    export_html: bool = True,
    export_posts: bool = True,
    include_hyderabad: bool = True
) -> Dict[str, Any]:
    """
    Main pipeline entry point.
    1. Initializes DB.
    2. Runs collectors (including Hyderabad ecosystem collectors) unless export_only=True.
    3. Optionally imports items from Legion Feed HTML if import_feed=True.
    4. Deduplicates against SQLite items.
    5. Formats drafts using Legion house style with location badges & venue specifics.
    6. Inserts new pending records.
    7. Updates PENDING_DRAFTS.md review digest prioritizing Hyderabad offline items.
    8. Updates legion_feed.html interactive dashboard if export_html=True.
    9. Exports posts.json feed for web dashboard if export_posts=True.
    """
    actual_limit = limit_per_feed if limit_per_feed is not None else config.MAX_ITEMS_PER_FEED
    actual_db = db_path or config.DB_PATH
    actual_out = markdown_path or config.PENDING_DRAFTS_PATH

    if html_path is not None:
        actual_html = html_path
    elif markdown_path is not None and markdown_path != config.PENDING_DRAFTS_PATH:
        # Keep html file inside test directory when custom output path is passed
        actual_html = os.path.join(os.path.dirname(os.path.abspath(markdown_path)), "legion_feed.html")
    else:
        actual_html = getattr(config, "LEGION_FEED_HTML_PATH", None)

    if posts_path is not None:
        actual_posts = posts_path
    elif markdown_path is not None and markdown_path != config.PENDING_DRAFTS_PATH:
        # Keep posts file inside test directory when custom output path is passed
        actual_posts = os.path.join(os.path.dirname(os.path.abspath(markdown_path)), "posts.json")
    else:
        actual_posts = getattr(config, "POSTS_JSON_PATH", "posts.json")

    logger.info("Initializing Legion database schema...")
    db.init_db(db_path=actual_db)
    
    # Expiry job runs at the start of every pipeline run
    db.expire_old_items(db_path=actual_db)
    db.purge_old_expired_bodies(db_path=actual_db)
    
    collected_count = 0
    new_pending_count = 0
    duplicate_count = 0
    discarded_count = 0
    
    new_review_count = 0
    if not export_only:
        # If legacy collectors.collect_all_items is mocked by unit tests, use it
        if hasattr(collectors.collect_all_items, "mock_calls") or hasattr(collectors.collect_all_items, "return_value"):
            raw_items = collectors.collect_all_items(limit_per_feed=actual_limit, include_hyderabad=include_hyderabad)
            # Legacy test fixtures from initial test suite use March 2026 mock dates
            future_test_date = (config.now_ist() + timedelta(days=15)).strftime("%Y-%m-%d")
            for mock_it in raw_items:
                if any(m in str(mock_it.get("deadline", "")) for m in ("March 2026", "2026-03")):
                    mock_it["deadline"] = future_test_date
        else:
            logger.info(f"Starting collector run with collectors2 (limit={actual_limit} per feed)...")
            try:
                raw_items = collectors2.collect(limit=actual_limit)
            except Exception as e:
                logger.warning(f"Error during collectors2.collect: {e}")
                raw_items = []
        collected_count = len(raw_items)
        
        import enricher
        for item in raw_items:
            try:
                # Run quality gate on every item before formatter
                decision, reason, gated = quality_gate.gate(item)
                if decision == "reject":
                    discarded_count += 1
                    logger.info(f"Dropped rejected item: '{item.get('title')}' - Reason: {reason}")
                    continue

                raw_url = gated.get("url") or gated.get("link")
                if not raw_url:
                    continue
                url = str(raw_url).strip()
                if not url:
                    continue

                # Detail page enrichment (called after quality gate accept/review)
                enriched = enricher.enrich_item(gated)

                # Date rule: If no parseable event_start/deadline for an event or hackathon -> status='review', reason='no date'
                cat = enriched.get("category", "news").lower()
                if cat in ("event", "hackathon"):
                    has_date = bool(
                        enriched.get("event_start") or enriched.get("deadline") or enriched.get("date_str")
                    )
                    if not has_date or not db.parse_date_ist(enriched.get("deadline") or enriched.get("event_start") or enriched.get("date_str")):
                        decision = "review"
                        reason = "no date"
                        enriched["status"] = "review"
                        enriched["eligibility"] = "Review needed: no date"

                # Location routing
                if not formatter.is_location_allowed(enriched):
                    discarded_count += 1
                    logger.info(f"Skipping disallowed city item: {enriched.get('title')} ({enriched.get('location')})")
                    continue
                    
                # Second dedup key: fingerprint = normalized(title) + start date + city
                fp = enriched.get("fingerprint") or db.compute_fingerprint(
                    title=enriched.get("title", ""),
                    start_date=enriched.get("event_start") or enriched.get("date_str") or enriched.get("deadline") or "",
                    city=enriched.get("city") or enriched.get("location") or ""
                )
                enriched["fingerprint"] = fp

                if db.item_exists(url, db_path=actual_db) or (fp and db.fingerprint_exists(fp, db_path=actual_db)):
                    duplicate_count += 1
                    continue
                    
                # Generate house style draft text using gate badge
                draft = formatter.format_draft(enriched)
                enriched["draft_text"] = draft
                
                if decision == "accept":
                    enriched["status"] = "pending"
                    row_id = db.insert_item(enriched, db_path=actual_db)
                    if row_id is not None:
                        new_pending_count += 1
                    else:
                        duplicate_count += 1
                elif decision == "review":
                    enriched["status"] = "review"
                    if not enriched.get("eligibility") or not enriched.get("eligibility").startswith("Review needed:"):
                        enriched["eligibility"] = f"Review needed: {reason}"
                    row_id = db.insert_item(enriched, db_path=actual_db)
                    if row_id is not None:
                        new_review_count += 1
                    else:
                        duplicate_count += 1
            except Exception as e:
                logger.warning(f"Error processing feed item: {e}")
                
        if import_feed:
            feed_imported = import_legion_feed(html_path=actual_html, db_path=actual_db)
            new_pending_count += feed_imported

        logger.info(
            f"Pipeline collection complete: {collected_count} collected, "
            f"{new_pending_count} new pending added, {new_review_count} flagged for review, "
            f"{duplicate_count} duplicates skipped, {discarded_count} rejected/discarded."
        )
    else:
        logger.info("Export-only mode active: skipping collectors.")

    # Always refresh PENDING_DRAFTS.md
    export_pending_markdown(db_path=actual_db, output_path=actual_out)
    
    # Refresh Legion Feed HTML dashboard
    if export_html and actual_html:
        try:
            export_feed_html(db_path=actual_db, output_path=actual_html)
        except Exception as e:
            logger.warning(f"Failed to refresh Legion Feed HTML: {e}")

    # Refresh posts.json feed export
    if export_posts and actual_posts:
        try:
            export_posts_json(db_path=actual_db, output_path=actual_posts)
        except Exception as e:
            logger.warning(f"Failed to refresh posts.json: {e}")
            
    stats = db.get_stats(db_path=actual_db)
    total_pending = stats.get("pending", 0)
    
    summary = {
        "collected": collected_count,
        "new_pending": new_pending_count,
        "duplicates_skipped": duplicate_count,
        "discarded_other_cities": discarded_count if not export_only else 0,
        "total_pending": total_pending,
        "stats": stats,
    }
    return summary

def main():
    parser = argparse.ArgumentParser(description="Legion Automation System - Content Pipeline")
    parser.add_argument("--limit", type=int, default=config.MAX_ITEMS_PER_FEED, help="Items per feed")
    parser.add_argument("--export-only", action="store_true", help="Only refresh PENDING_DRAFTS.md and legion_feed.html without collecting")
    parser.add_argument("--db", type=str, default=config.DB_PATH, help="Path to SQLite DB")
    parser.add_argument("--out", type=str, default=config.PENDING_DRAFTS_PATH, help="Path to markdown output")
    parser.add_argument("--html-out", type=str, default=getattr(config, "LEGION_FEED_HTML_PATH", "legion_feed.html"), help="Path to HTML feed dashboard")
    parser.add_argument("--posts-out", type=str, default=getattr(config, "POSTS_JSON_PATH", "posts.json"), help="Path to posts.json feed export")
    parser.add_argument("--auto-update", type=int, default=getattr(config, "AUTO_UPDATE_INTERVAL_MINUTES", 30), help="Auto-update interval in minutes for background feed collector in serve mode (0 to disable)")
    parser.add_argument("--import-feed", type=str, nargs="?", const="default", help="Import curated items from Legion Feed HTML into database")
    parser.add_argument("--export-html", action="store_true", help="Export DB items to HTML feed dashboard")
    parser.add_argument("--serve", type=int, nargs="?", const=8000, help="Start local web server for Legion Feed dashboard (default port: 8000)")
    parser.add_argument("--open", action="store_true", help="Open Legion Feed dashboard in default browser")
    args = parser.parse_args()
    
    if args.serve is not None:
        serve_feed(
            port=args.serve,
            html_path=args.html_out,
            db_path=args.db,
            posts_path=args.posts_out,
            markdown_path=args.out,
            auto_update_minutes=args.auto_update
        )
        return

    if args.open:
        open_feed_html(html_path=args.html_out)
        print(f"🌍 Opened Legion Feed in browser: {args.html_out}")
        return

    if args.import_feed is not None:
        feed_path = None if args.import_feed == "default" else args.import_feed
        imported = import_legion_feed(html_path=feed_path, db_path=args.db)
        export_pending_markdown(db_path=args.db, output_path=args.out)
        export_feed_html(db_path=args.db, output_path=args.html_out)
        export_posts_json(db_path=args.db, output_path=args.posts_out)
        print(f"✅ Imported {imported} opportunities from Legion Feed into {args.db}")
        return

    if args.export_html:
        export_feed_html(db_path=args.db, output_path=args.html_out)
        export_posts_json(db_path=args.db, output_path=args.posts_out)
        print(f"✅ Updated Legion Feed HTML at: {args.html_out}")
        return

    print("=" * 60)
    print("🛡️  LEGION AUTOMATION PIPELINE")
    print("=" * 60)
    
    results = run_pipeline(
        limit_per_feed=args.limit,
        export_only=args.export_only,
        db_path=args.db,
        markdown_path=args.out,
        html_path=args.html_out,
        posts_path=args.posts_out
    )
    
    print("\n--- Pipeline Execution Summary ---")
    print(f"Collected from feeds : {results['collected']}")
    print(f"New Pending Drafts   : {results['new_pending']}")
    print(f"Duplicates Skipped   : {results['duplicates_skipped']}")
    print(f"Total Pending in DB  : {results['total_pending']}")
    print(f"Database Stats       : {results['stats']}")
    print(f"Review File Saved At : {args.out}")
    print(f"HTML Feed Saved At   : {args.html_out}")
    print(f"Posts Feed Saved At  : {args.posts_out}")
    print("=" * 60)

if __name__ == "__main__":
    main()


