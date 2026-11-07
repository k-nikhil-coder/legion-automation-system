"""
Legion Automation System - Content Pipeline
Orchestrates collection, deduplication, formatting, database storage,
and exports the updated PENDING_DRAFTS.md review digest.
"""

import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime
from typing import List, Dict, Any, Optional


import config
import db
import formatter
import collectors

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
                draft = it.get("draft_text", "")
                created_at = it.get("created_at", "")
                loc_tag = formatter.get_location_tag(it)
                
                lines.append(f"### [#{item_id}] {title}")
                lines.append(f"- **Location**: {loc_tag} | **Source**: {source} | **Created**: `{created_at}`")
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
                loc_tag = formatter.get_location_tag(it)
                lines.append(f"### [#{item_id}] {it.get('title')}")
                lines.append(f"- **Location**: {loc_tag} | **Category**: {it.get('category')} | **Source**: {it.get('source')}")
                lines.append(f"- **Quick Commands**: `python telegram_bot.py --approve {item_id}` | `python telegram_bot.py --reject {item_id}` | `python telegram_bot.py --edit {item_id}`")
                lines.append("```text")
                lines.append(it.get("draft_text", ""))
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
    
    # 3. Fetch items from DB
    db_items: List[Dict[str, Any]] = []
    try:
        with db.get_db_connection(actual_db) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM items ORDER BY id DESC LIMIT 150")
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
            created_str = datetime.now().strftime("%Y-%m-%d")
            
        seed_list.append({
            "id": f"db_{it.get('id')}",
            "type": it.get("category", "news"),
            "title": it.get("title", ""),
            "sum": it.get("summary") or it.get("title", ""),
            "date": clean_date,
            "where": it.get("location") or "Online",
            "src": it.get("source") or "Legion Automation",
            "link": u,
            "posted": created_str,
            "status": it.get("status", "pending")
        })
        
    # Add existing template items that are not already present
    for orig in existing_items:
        u = orig.get("url", "").strip()
        if u and u not in seen_urls:
            seen_urls.add(u)
            raw_d = orig.get("date_str") or orig.get("deadline") or ""
            d_m = re.search(r'\d{4}-\d{2}-\d{2}', str(raw_d))
            clean_d = d_m.group(0) if d_m else (raw_d if len(raw_d) == 10 and "-" in raw_d else "")
            seed_list.append({
                "id": orig.get("url", "").split("/")[-1] or f"s{len(seed_list)+1}",
                "type": orig.get("category", "news"),
                "title": orig.get("title", ""),
                "sum": orig.get("summary", ""),
                "date": clean_d,
                "where": orig.get("location", "Online"),
                "src": orig.get("source", "Community"),
                "link": u,
                "posted": datetime.now().strftime("%Y-%m-%d"),
                "status": "seed"
            })

    # Also ensure CURATED_SEED_ITEMS are preserved if not yet in seed_list
    for seed_curated in getattr(collectors, "CURATED_SEED_ITEMS", []):
        u = seed_curated.get("link", "").strip() or f"https://legion.community/feed/{seed_curated.get('id')}"
        if u not in seen_urls:
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

def serve_feed(port: int = 8000, html_path: Optional[str] = None):
    """Starts a local HTTP server serving the Legion Feed dashboard."""
    import http.server
    import socketserver
    import webbrowser

    actual_path = html_path or getattr(config, "LEGION_FEED_HTML_PATH", "legion_feed.html")
    directory = os.path.dirname(os.path.abspath(actual_path))
    filename = os.path.basename(actual_path)

    class CustomHandler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=directory, **kwargs)

        def do_GET(self):
            if self.path in ("/", "/index.html", "/feed"):
                self.path = f"/{filename}"
            return super().do_GET()

    with socketserver.TCPServer(("", port), CustomHandler) as httpd:
        url = f"http://localhost:{port}/{filename}"
        print(f"🌍 Legion Feed Web Server running at: {url}")
        print("Press Ctrl+C to stop.")
        try:
            webbrowser.open(url)
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down Legion Feed Web Server...")

def run_pipeline(
    limit_per_feed: Optional[int] = None,
    export_only: bool = False,
    db_path: Optional[str] = None,
    markdown_path: Optional[str] = None,
    html_path: Optional[str] = None,
    import_feed: bool = False,
    export_html: bool = True,
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


    logger.info("Initializing Legion database schema...")
    db.init_db(db_path=actual_db)
    
    collected_count = 0
    new_pending_count = 0
    duplicate_count = 0
    discarded_count = 0
    
    if not export_only:
        logger.info(f"Starting collector run (limit={actual_limit} per feed, hyderabad={include_hyderabad})...")
        raw_items = collectors.collect_all_items(limit_per_feed=actual_limit, include_hyderabad=include_hyderabad)
        collected_count = len(raw_items)
        
        for item in raw_items:
            try:
                raw_url = item.get("url")
                if not raw_url:
                    continue
                url = str(raw_url).strip()
                if not url:
                    continue

                # Location routing: Discard other cities (Bangalore, Delhi, etc.) unless online/remote or national
                if not formatter.is_location_allowed(item):
                    discarded_count += 1
                    logger.info(f"Skipping disallowed city item: {item.get('title')} ({item.get('location')})")
                    continue
                    
                if db.item_exists(url, db_path=actual_db):
                    duplicate_count += 1
                    continue
                    
                # Generate house style draft text
                draft = formatter.format_draft(item)
                item["draft_text"] = draft
                item["status"] = "pending"
                
                row_id = db.insert_item(item, db_path=actual_db)
                if row_id is not None:
                    new_pending_count += 1
                else:
                    duplicate_count += 1
            except Exception as e:
                logger.warning(f"Error processing feed item: {e}")
                
        if import_feed:
            feed_imported = import_legion_feed(html_path=actual_html, db_path=actual_db)
            new_pending_count += feed_imported

        logger.info(
            f"Pipeline collection complete: {collected_count} collected, "
            f"{new_pending_count} new pending added, {duplicate_count} duplicates skipped, "
            f"{discarded_count} other-city items discarded."
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
    parser.add_argument("--import-feed", type=str, nargs="?", const="default", help="Import curated items from Legion Feed HTML into database")
    parser.add_argument("--export-html", action="store_true", help="Export DB items to HTML feed dashboard")
    parser.add_argument("--serve", type=int, nargs="?", const=8000, help="Start local web server for Legion Feed dashboard (default port: 8000)")
    parser.add_argument("--open", action="store_true", help="Open Legion Feed dashboard in default browser")
    args = parser.parse_args()
    
    if args.serve is not None:
        serve_feed(port=args.serve, html_path=args.html_out)
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
        print(f"✅ Imported {imported} opportunities from Legion Feed into {args.db}")
        return

    if args.export_html:
        export_feed_html(db_path=args.db, output_path=args.html_out)
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
        html_path=args.html_out
    )
    
    print("\n--- Pipeline Execution Summary ---")
    print(f"Collected from feeds : {results['collected']}")
    print(f"New Pending Drafts   : {results['new_pending']}")
    print(f"Duplicates Skipped   : {results['duplicates_skipped']}")
    print(f"Total Pending in DB  : {results['total_pending']}")
    print(f"Database Stats       : {results['stats']}")
    print(f"Review File Saved At : {args.out}")
    print(f"HTML Feed Saved At   : {args.html_out}")
    print("=" * 60)

if __name__ == "__main__":
    main()

