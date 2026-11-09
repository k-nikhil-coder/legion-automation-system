"""
Legion Automation System - Telegram Bot & Approval Workflow
Provides inline keyboard approvals (Approve, Edit, Reject, Mark Done/Posted)
via Telegram Bot API, with automatic fallback to local markdown review queue.
"""

import argparse
import html
import json
import logging
import os
import sys
import time
import urllib.request
import urllib.error
from typing import Dict, Any, List, Optional

import config
import db
import formatter
import pipeline

# Ensure stdout handles UTF-8 on Windows
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

logger = logging.getLogger("legion.telegram")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s] %(message)s")

class TelegramClient:
    """Lightweight standard library Telegram Bot API client."""
    
    def __init__(self, token: str):
        self.token = token.strip()
        self.api_url = f"https://api.telegram.org/bot{self.token}"
        
    def _post(self, method: str, payload: Dict[str, Any], timeout: int = 20) -> Optional[Dict[str, Any]]:
        if not self.token:
            return None
        url = f"{self.api_url}/{method}"
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                res = json.loads(resp.read().decode("utf-8"))
                return res
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="ignore")
            logger.error(f"Telegram API HTTP {e.code} on {method}: {err_body}")
            return None
        except Exception as e:
            logger.error(f"Telegram API request failed on {method}: {e}")
            return None

    def send_message(
        self,
        chat_id: str,
        text: str,
        reply_markup: Optional[Dict[str, Any]] = None,
        parse_mode: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        # Telegram max message length is 4096 chars; truncate safely if needed
        safe_text = text if len(text) <= 4000 else text[:3997] + "..."
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "text": safe_text,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        if parse_mode:
            payload["parse_mode"] = parse_mode
        res = self._post("sendMessage", payload)
        
        # If request failed and reply_markup contains copy_text, retry with standard buttons
        if not res and reply_markup and "inline_keyboard" in reply_markup:
            has_copy_text = any(
                "copy_text" in btn
                for row in reply_markup["inline_keyboard"]
                for btn in row
            )
            if has_copy_text:
                fallback_kb = {
                    "inline_keyboard": [
                        [btn for btn in row if "copy_text" not in btn]
                        for row in reply_markup["inline_keyboard"]
                    ]
                }
                fallback_kb["inline_keyboard"] = [r for r in fallback_kb["inline_keyboard"] if r]
                payload["reply_markup"] = fallback_kb
                res = self._post("sendMessage", payload)
                
        return res

    def edit_message_text(
        self,
        chat_id: str,
        message_id: int,
        text: str,
        reply_markup: Optional[Dict[str, Any]] = None,
        parse_mode: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        safe_text = text if len(text) <= 4000 else text[:3997] + "..."
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": safe_text,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        if parse_mode:
            payload["parse_mode"] = parse_mode
        return self._post("editMessageText", payload)

    def answer_callback_query(
        self,
        callback_query_id: str,
        text: Optional[str] = None,
        show_alert: bool = False
    ) -> Optional[Dict[str, Any]]:
        payload: Dict[str, Any] = {
            "callback_query_id": callback_query_id,
            "show_alert": show_alert
        }
        if text:
            payload["text"] = text
        return self._post("answerCallbackQuery", payload)

    def get_updates(self, offset: Optional[int] = None, timeout: int = 15) -> List[Dict[str, Any]]:
        payload: Dict[str, Any] = {"timeout": timeout}
        if offset is not None:
            payload["offset"] = offset
        res = self._post("getUpdates", payload, timeout=timeout + 5)
        if res and res.get("ok"):
            return res.get("result", [])
        return []

def get_approval_keyboard(item_id: int, draft_text: str = "") -> Dict[str, Any]:
    """
    Generates inline approval keyboard for a draft item.
    Includes Approve, Reject, Edit, Mark Done/Posted, and 1-tap Copy Draft.
    """
    buttons = [
        [
            {"text": "✅ Approve", "callback_data": f"approve:{item_id}"},
            {"text": "❌ Reject", "callback_data": f"reject:{item_id}"}
        ],
        [
            {"text": "✏️ Edit Draft", "callback_data": f"edit:{item_id}"},
            {"text": "🚀 Mark Done / Posted", "callback_data": f"done:{item_id}"}
        ]
    ]
    if draft_text:
        # Telegram Bot API 7.5+ 1-tap copy to clipboard button
        buttons.append([
            {"text": "📋 Copy Draft to Clipboard", "copy_text": {"text": draft_text}}
        ])
    return {"inline_keyboard": buttons}

def get_post_keyboard(item_id: int, draft_text: str = "") -> Dict[str, Any]:
    """Generates keyboard after an item has been approved."""
    buttons = [
        [
            {"text": "🚀 Copied & Posted to WhatsApp", "callback_data": f"done:{item_id}"},
            {"text": "❌ Move to Rejected", "callback_data": f"reject:{item_id}"}
        ]
    ]
    if draft_text:
        buttons.append([
            {"text": "📋 Copy Draft to Clipboard", "copy_text": {"text": draft_text}}
        ])
    return {"inline_keyboard": buttons}

def format_telegram_card(item: Dict[str, Any]) -> str:
    """Formats an item for Telegram presentation."""
    item_id = item.get("id")
    category = item.get("category", "news")
    target_wa = formatter.get_whatsapp_target_group(category)
    draft = item.get("draft_text") or formatter.format_draft(item)
    source = item.get("source", "Feed")
    url = item.get("url", "")
    loc_badge = formatter.get_location_badge(item)
    loc_tag = formatter.get_location_tag(item)
    
    msg = (
        f"🛡️ [Legion Review #{item_id}] {loc_badge}\n"
        f"📂 Category: {category.upper()}\n"
        f"{loc_tag}\n"
        f"🎯 Target WhatsApp Group: {target_wa}\n"
        f"🌐 Source: {source}\n\n"
        f"--- COPY-READY DRAFT ---\n"
        f"{draft}\n\n"
        f"🔗 Original Link: {url}"
    )
    return msg

def send_health_alert(text: str, token: Optional[str] = None, chat_id: Optional[str] = None) -> bool:
    """Sends a system alert message via Telegram."""
    bot_token = token or config.TELEGRAM_BOT_TOKEN
    target_chat = chat_id or config.TELEGRAM_CHAT_ID
    if not bot_token or not target_chat:
        return False
    bot = TelegramClient(bot_token)
    res = bot.send_message(target_chat, text)
    return bool(res and res.get("ok"))

def push_pending_to_telegram(
    bot: TelegramClient,
    chat_id: str,
    limit: int = 10,
    db_path: str = config.DB_PATH
) -> int:
    """Pushes active unexpired pending drafts to Telegram chat with approval buttons."""
    now_str = config.now_ist().strftime("%Y-%m-%d %H:%M:%S")
    raw_pending = db.get_pending_items(limit=limit, db_path=db_path)
    pending = [
        it for it in raw_pending
        if it.get("status") in ("accepted", "review", "pending") and (not it.get("expires_at") or it.get("expires_at") > now_str)
    ]
    if not pending:
        logger.info("No pending items to push.")
        return 0
        
    pushed = 0
    for item in pending:
        card_text = format_telegram_card(item)
        draft = item.get("draft_text", "")
        kb = get_approval_keyboard(item["id"], draft_text=draft)
        res = bot.send_message(chat_id, card_text, reply_markup=kb)
        if res and res.get("ok"):
            pushed += 1
            time.sleep(0.3)  # Rate limiting courtesy
        else:
            logger.warning(f"Failed to send item #{item['id']} to Telegram.")
            
    logger.info(f"Pushed {pushed} items to Telegram chat {chat_id}.")
    return pushed

def handle_callback_query(bot: TelegramClient, query: Dict[str, Any], db_path: str = config.DB_PATH):
    """Processes callback button clicks (approve, reject, done, edit)."""
    query_id = query.get("id")
    data = query.get("data", "")
    message = query.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    message_id = message.get("message_id")
    original_text = message.get("text", "")
    
    if ":" not in data:
        bot.answer_callback_query(query_id, text="Invalid action")
        return
        
    action, item_id_str = data.split(":", 1)
    try:
        item_id = int(item_id_str)
    except ValueError:
        bot.answer_callback_query(query_id, text="Invalid item ID")
        return

    if action == "approve":
        success = db.update_item_status(item_id, "approved", db_path=db_path)
        if success:
            pipeline.export_pending_markdown(db_path=db_path)
            item = db.get_item(item_id, db_path=db_path)
            draft = item.get("draft_text", "") if item else ""
            bot.answer_callback_query(query_id, text=f"Item #{item_id} APPROVED! Ready for WhatsApp.")
            new_text = f"✅ [APPROVED FOR WHATSAPP]\n\n" + original_text
            bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=new_text,
                reply_markup=get_post_keyboard(item_id, draft_text=draft)
            )
        else:
            bot.answer_callback_query(query_id, text=f"Failed to update #{item_id}")

    elif action == "reject":
        success = db.update_item_status(item_id, "rejected", db_path=db_path)
        if success:
            pipeline.export_pending_markdown(db_path=db_path)
            bot.answer_callback_query(query_id, text=f"Item #{item_id} REJECTED.")
            new_text = f"❌ [REJECTED]\n\n" + original_text
            bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=new_text,
                reply_markup={"inline_keyboard": []}
            )
        else:
            bot.answer_callback_query(query_id, text=f"Failed to update #{item_id}")

    elif action == "done":
        success = db.update_item_status(item_id, "posted", db_path=db_path)
        if success:
            pipeline.export_pending_markdown(db_path=db_path)
            bot.answer_callback_query(query_id, text=f"Item #{item_id} marked as POSTED!")
            new_text = f"🚀 [POSTED TO WHATSAPP]\n\n" + original_text
            bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=new_text,
                reply_markup={"inline_keyboard": []}
            )
        else:
            bot.answer_callback_query(query_id, text=f"Failed to update #{item_id}")

    elif action == "edit":
        item = db.get_item(item_id, db_path=db_path)
        draft = item.get("draft_text", "") if item else ""
        bot.answer_callback_query(
            query_id,
            text=f"Editing #{item_id}. See instructions below.",
            show_alert=False
        )
        edit_help = (
            f"✏️ *Editing Draft #{item_id}*\n\n"
            f"Reply with your updated text using this command:\n"
            f"`/edit {item_id} <updated post draft text>`\n\n"
            f"*Current Draft Text (copy and modify):*\n"
            f"```text\n{draft}\n```\n\n"
            f"_Or via CLI:_ `python telegram_bot.py --edit {item_id} --text \"...\"`"
        )
        bot.send_message(str(chat_id), edit_help, parse_mode="Markdown")

def handle_text_command(bot: TelegramClient, message: Dict[str, Any], db_path: str = config.DB_PATH):
    """Handles text commands from admin chat (/start, /status, /pending, /push, /edit)."""
    chat_id = message.get("chat", {}).get("id")
    text = (message.get("text") or "").strip()
    
    if text in ("/start", "/help"):
        welcome = (
            "🛡️ *Legion Automation Bot*\n\n"
            "Commands:\n"
            "• `/status` - View current queue statistics\n"
            "• `/pending` - View pending draft count\n"
            "• `/push` - Push next batch of pending drafts for review\n"
            "• `/edit <id> <text>` - Update draft text for an item\n"
            "• `/refresh` - Re-export PENDING_DRAFTS.md\n"
        )
        bot.send_message(chat_id, welcome, parse_mode="Markdown")
        
    elif text == "/status":
        stats = db.get_stats(db_path=db_path)
        msg = (
            "📊 *Legion Content Queue Stats:*\n\n"
            f"⏳ Pending: {stats.get('pending', 0)}\n"
            f"✅ Approved: {stats.get('approved', 0)}\n"
            f"❌ Rejected: {stats.get('rejected', 0)}\n"
            f"🚀 Posted: {stats.get('posted', 0)}\n"
        )
        bot.send_message(chat_id, msg, parse_mode="Markdown")

    elif text in ("/pending", "/push"):
        stats = db.get_stats(db_path=db_path)
        pending_count = stats.get("pending", 0)
        bot.send_message(chat_id, f"Found {pending_count} pending drafts. Sending next batch...")
        pushed = push_pending_to_telegram(bot, str(chat_id), limit=5, db_path=db_path)
        if pushed == 0:
            bot.send_message(chat_id, "🎉 All caught up! No pending drafts.")

    elif text.startswith("/edit"):
        parts = text.split(maxsplit=2)
        if len(parts) < 3:
            bot.send_message(
                chat_id,
                "⚠️ *Usage:* `/edit <ID> <new draft text>`\n\nExample:\n`/edit 5 🎓 New Course Title\\n\\n📌 What: ...`",
                parse_mode="Markdown"
            )
            return
        try:
            target_id = int(parts[1])
        except ValueError:
            bot.send_message(chat_id, "❌ *Error:* ID must be a numeric item ID.", parse_mode="Markdown")
            return
        new_draft = parts[2].strip()
        if db.update_item_draft(target_id, new_draft, db_path=db_path):
            pipeline.export_pending_markdown(db_path=db_path)
            item = db.get_item(target_id, db_path=db_path)
            bot.send_message(chat_id, f"✅ Draft #{target_id} updated successfully!\n\nUpdated preview:")
            if item:
                card_text = format_telegram_card(item)
                kb = get_approval_keyboard(target_id, draft_text=new_draft)
                bot.send_message(chat_id, card_text, reply_markup=kb)
        else:
            bot.send_message(chat_id, f"❌ Failed to find or update Item #{target_id}.")

    elif text == "/refresh":
        pipeline.export_pending_markdown(db_path=db_path)
        try:
            pipeline.export_feed_html(db_path=db_path)
        except Exception:
            pass
        bot.send_message(chat_id, "✅ PENDING_DRAFTS.md and legion_feed.html refreshed successfully.")

    elif text in ("/feed", "/web"):
        feed_path = getattr(config, "LEGION_FEED_HTML_PATH", "legion_feed.html")
        stats = db.get_stats(db_path=db_path)
        bot.send_message(
            chat_id,
            f"🌍 *Legion Feed Web Dashboard*\n\n"
            f"Interactive dashboard with category filters & 1-tap WhatsApp copying!\n"
            f"📍 File: `{feed_path}`\n\n"
            f"⏳ Pending: {stats.get('pending', 0)} | ✅ Approved: {stats.get('approved', 0)}\n"
            f"Run `python pipeline.py --open` to view in your browser."
        )


def poll_telegram_updates(bot: TelegramClient, db_path: str = config.DB_PATH):
    """Continuous polling loop for incoming approvals and commands."""
    logger.info("Starting Telegram Bot long-polling loop... Press Ctrl+C to stop.")
    offset = None
    while True:
        try:
            updates = bot.get_updates(offset=offset, timeout=10)
            for upd in updates:
                offset = upd.get("update_id", 0) + 1
                if "callback_query" in upd:
                    handle_callback_query(bot, upd["callback_query"], db_path=db_path)
                elif "message" in upd:
                    handle_text_command(bot, upd["message"], db_path=db_path)
        except KeyboardInterrupt:
            logger.info("Polling stopped by user.")
            break
        except Exception as e:
            logger.error(f"Error in polling loop: {e}")
            time.sleep(3)

def run_cli_interactive_review(db_path: str = config.DB_PATH):
    """Terminal fallback approval interface for environments without Telegram bot."""
    pending = db.get_pending_items(limit=50, db_path=db_path)
    if not pending:
        print("\n🎉 No pending drafts awaiting review!")
        return
        
    print(f"\nFound {len(pending)} pending drafts. Starting Interactive Review...\n")
    for it in pending:
        item_id = it["id"]
        category = it.get("category", "news")
        target_wa = formatter.get_whatsapp_target_group(category)
        loc_badge = formatter.get_location_badge(it)
        loc_tag = formatter.get_location_tag(it)
        
        print("=" * 65)
        print(f"Item #{item_id} | Category: {category.upper()} | {loc_badge}")
        print(f"{loc_tag} | Target: {target_wa}")
        print(f"Source: {it.get('source')} | Title: {it.get('title')}")
        print("-" * 65)
        cli_draft = it.get("draft_text") or formatter.format_draft(it)
        print(cli_draft)
        print("-" * 65)
        
        while True:
            choice = input("\n[a]pprove / [r]eject / [p]osted to WA / [e]dit / [s]kip / [q]uit: ").strip().lower()
            if choice == "a":
                db.update_item_status(item_id, "approved", db_path=db_path)
                print(f"✅ Item #{item_id} marked as APPROVED.")
                break
            elif choice == "r":
                db.update_item_status(item_id, "rejected", db_path=db_path)
                print(f"❌ Item #{item_id} marked as REJECTED.")
                break
            elif choice == "p":
                db.update_item_status(item_id, "posted", db_path=db_path)
                print(f"🚀 Item #{item_id} marked as POSTED.")
                break
            elif choice == "e":
                print("\nEnter updated draft text (type END on a single line when done):")
                lines = []
                while True:
                    try:
                        line = input()
                        if line.strip() == "END":
                            break
                        lines.append(line)
                    except EOFError:
                        break
                new_text = "\n".join(lines).strip()
                if new_text:
                    db.update_item_draft(item_id, new_text, db_path=db_path)
                    print(f"✅ Draft #{item_id} updated.")
                else:
                    print("Edit cancelled (empty text).")
            elif choice == "s":
                print(f"Skipping #{item_id}.")
                break
            elif choice == "q":
                print("Exiting interactive review.")
                pipeline.export_pending_markdown(db_path=db_path)
                return
            else:
                print("Invalid input. Please choose 'a', 'r', 'p', 'e', 's', or 'q'.")
                
    pipeline.export_pending_markdown(db_path=db_path)
    print("\n✅ Review complete. PENDING_DRAFTS.md updated.")

def main():
    parser = argparse.ArgumentParser(description="Legion Telegram Approval Bot & Review Workflow")
    parser.add_argument("--push", action="store_true", help="Push pending items to Telegram and exit")
    parser.add_argument("--poll", action="store_true", help="Run polling loop for Telegram approvals")
    parser.add_argument("--review", action="store_true", help="Run local CLI interactive review")
    parser.add_argument("--status", action="store_true", help="Show database queue statistics")
    parser.add_argument("--approve", type=int, help="Approve item by ID")
    parser.add_argument("--reject", type=int, help="Reject item by ID")
    parser.add_argument("--posted", type=int, help="Mark item as posted by ID")
    parser.add_argument("--edit", type=int, help="Edit draft text for item by ID")
    parser.add_argument("--text", type=str, default=None, help="New draft text (used with --edit)")
    parser.add_argument("--feed", "--web", dest="open_feed", action="store_true", help="Open Legion Feed dashboard in default browser")
    parser.add_argument("--db", type=str, default=config.DB_PATH, help="Path to SQLite DB")
    args = parser.parse_args()
    
    if args.open_feed:
        pipeline.open_feed_html()
        feed_path = getattr(config, "LEGION_FEED_HTML_PATH", "legion_feed.html")
        print(f"🌍 Opened Legion Feed in browser: {feed_path}")
        return

    # 1. Quick status check
    if args.status:

        stats = db.get_stats(db_path=args.db)
        print("\n📊 Database Status Overview:")
        for status, count in stats.items():
            print(f"  • {status.capitalize():10}: {count}")
        return

    # 2. Direct CLI item status updates
    if args.approve:
        if db.update_item_status(args.approve, "approved", db_path=args.db):
            pipeline.export_pending_markdown(db_path=args.db)
            print(f"✅ Item #{args.approve} marked as APPROVED.")
        else:
            print(f"❌ Failed to find or update Item #{args.approve}.")
        return

    if args.reject:
        if db.update_item_status(args.reject, "rejected", db_path=args.db):
            pipeline.export_pending_markdown(db_path=args.db)
            print(f"❌ Item #{args.reject} marked as REJECTED.")
        else:
            print(f"❌ Failed to find or update Item #{args.reject}.")
        return

    if args.posted:
        if db.update_item_status(args.posted, "posted", db_path=args.db):
            pipeline.export_pending_markdown(db_path=args.db)
            print(f"🚀 Item #{args.posted} marked as POSTED.")
        else:
            print(f"❌ Failed to find or update Item #{args.posted}.")
        return

    if args.edit:
        item = db.get_item(args.edit, db_path=args.db)
        if not item:
            print(f"❌ Item #{args.edit} not found in database.")
            return
        if args.text is not None:
            new_text = args.text
        else:
            print(f"\n--- Current Draft for Item #{args.edit} ---")
            print(item.get("draft_text", ""))
            print("-" * 50)
            print("Enter new draft text (type END on a single line when done):")
            lines = []
            while True:
                try:
                    line = input()
                    if line.strip() == "END":
                        break
                    lines.append(line)
                except EOFError:
                    break
            new_text = "\n".join(lines).strip()
            if not new_text:
                print("Draft text was empty. Edit cancelled.")
                return

        if db.update_item_draft(args.edit, new_text, db_path=args.db):
            pipeline.export_pending_markdown(db_path=args.db)
            print(f"✅ Item #{args.edit} draft updated successfully.")
        else:
            print(f"❌ Failed to update Item #{args.edit}.")
        return

    # 3. Interactive CLI Review
    if args.review:
        run_cli_interactive_review(db_path=args.db)
        return

    # 4. Telegram Workflow
    token = config.TELEGRAM_BOT_TOKEN
    chat_id = config.TELEGRAM_CHAT_ID

    if not token or not chat_id:
        print("\n" + "=" * 65)
        print("⚠️  TELEGRAM BOT NOT CONFIGURED")
        print("=" * 65)
        print("Telegram credentials (TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID) are missing.")
        print("\nFallback Options:")
        print("1. Local Review Queue: Inspect and copy drafts from:")
        print(f"   {config.PENDING_DRAFTS_PATH}")
        print("2. Interactive Terminal Review: Run:")
        print("   python telegram_bot.py --review")
        print("3. Manual CLI Status Changes: Run:")
        print("   python telegram_bot.py --approve <ID> | --reject <ID> | --posted <ID> | --edit <ID>")
        print("\nTo enable full Telegram approval buttons:")
        print("1. Message @BotFather on Telegram to obtain a Bot Token.")
        print("2. Add TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to .env file.")
        print("=" * 65 + "\n")
        return

    bot = TelegramClient(token)

    if args.push:
        pushed = push_pending_to_telegram(bot, chat_id, db_path=args.db)
        print(f"Pushed {pushed} pending drafts to Telegram chat {chat_id}.")
        return

    if args.poll:
        poll_telegram_updates(bot, db_path=args.db)
        return

    # Default action if both credentials exist
    print("Telegram configured! Pushing pending items and starting listener...")
    push_pending_to_telegram(bot, chat_id, db_path=args.db)
    poll_telegram_updates(bot, db_path=args.db)

if __name__ == "__main__":
    main()
