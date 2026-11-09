"""
Comprehensive Test Suite for Legion Automation System
Tests db, formatter, collectors, pipeline, telegram_bot, and sidecar configuration.
"""

import json
import os
import sys
import sqlite3
import socketserver
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import MagicMock, patch

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from datetime import datetime, timedelta
import config
import db
import formatter
import collectors
import collectors2
import pipeline
import telegram_bot
import quality_gate
import enricher

class TestDatabase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_legion.db")
        db.init_db(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_init_db(self):
        with db.get_db_connection(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='items';")
            self.assertIsNotNone(cursor.fetchone())

    def test_normalize_url(self):
        url1 = "https://DevPost.com/hackathons/global-ai/?utm_source=rss&utm_medium=feed&ref=123#overview"
        norm1 = db.normalize_url(url1)
        self.assertEqual(norm1, "https://devpost.com/hackathons/global-ai")

        url2 = "https://example.com/item/test/"
        norm2 = db.normalize_url(url2)
        self.assertEqual(norm2, "https://example.com/item/test")

        # Keep non-tracking query parameters intact
        url3 = "https://example.com/api?id=42&page=1"
        norm3 = db.normalize_url(url3)
        self.assertIn("id=42", norm3)
        self.assertIn("page=1", norm3)

        # Empty / None URL handling
        self.assertEqual(db.normalize_url(""), "")
        self.assertEqual(db.normalize_url(None), "")

    def test_insert_and_exists(self):
        item = {
            "source": "Devpost",
            "category": "hackathon",
            "title": "Global AI Hackathon",
            "url": "https://devpost.com/hackathons/global-ai?utm_source=rss",
            "summary": "Build innovative AI solutions",
            "date_str": "Nov 2026",
            "deadline": "Nov 30, 2026",
            "location": "Online",
            "eligibility": "Open to all",
            "draft_text": "Draft content here",
            "status": "pending"
        }
        self.assertFalse(db.item_exists(item["url"], self.db_path))
        
        row_id = db.insert_item(item, self.db_path)
        self.assertIsNotNone(row_id)
        # Should match both raw and normalized URL
        self.assertTrue(db.item_exists(item["url"], self.db_path))
        self.assertTrue(db.item_exists("https://devpost.com/hackathons/global-ai", self.db_path))

        # Duplicate insertion (even with different tracking params) should return None
        dup_item = dict(item)
        dup_item["url"] = "https://devpost.com/hackathons/global-ai?utm_campaign=winter"
        dup_id = db.insert_item(dup_item, self.db_path)
        self.assertIsNone(dup_id)

    def test_insert_item_invalid_urls(self):
        # Missing or None URL
        self.assertIsNone(db.insert_item({}, self.db_path))
        self.assertIsNone(db.insert_item({"url": None}, self.db_path))
        self.assertIsNone(db.insert_item({"url": "   "}, self.db_path))

    def test_get_item_and_update_draft(self):
        item = {
            "source": "Devpost",
            "category": "hackathon",
            "title": "Hackathon Beta",
            "url": "https://example.com/beta",
            "draft_text": "Original draft",
            "status": "pending"
        }
        item_id = db.insert_item(item, self.db_path)
        self.assertIsNotNone(item_id)

        fetched = db.get_item(item_id, self.db_path)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched["title"], "Hackathon Beta")
        self.assertEqual(fetched["draft_text"], "Original draft")

        # Update draft
        updated = db.update_item_draft(item_id, "Updated draft content", self.db_path)
        self.assertTrue(updated)

        refetched = db.get_item(item_id, self.db_path)
        self.assertEqual(refetched["draft_text"], "Updated draft content")

        # Non-existent item
        self.assertIsNone(db.get_item(9999, self.db_path))
        self.assertFalse(db.update_item_draft(9999, "new text", self.db_path))

    def test_get_pending_and_update_status(self):
        for i in range(3):
            db.insert_item({
                "source": "Test",
                "category": "news",
                "title": f"News Item {i}",
                "url": f"https://example.com/news/{i}",
                "summary": f"Summary {i}",
            }, self.db_path)

        pending = db.get_pending_items(limit=10, db_path=self.db_path)
        self.assertEqual(len(pending), 3)

        target_id = pending[0]["id"]
        success = db.update_item_status(target_id, "approved", self.db_path)
        self.assertTrue(success)

        stats = db.get_stats(self.db_path)
        self.assertEqual(stats.get("pending"), 2)
        self.assertEqual(stats.get("approved"), 1)

class TestFormatter(unittest.TestCase):
    def test_clean_text(self):
        self.assertEqual(formatter.clean_text(""), "")
        self.assertEqual(formatter.clean_text(None), "")
        raw = "Line one\n  Line two\r\n\tLine three   "
        self.assertEqual(formatter.clean_text(raw, 50), "Line one Line two Line three")
        long_str = "a" * 150
        truncated = formatter.clean_text(long_str, 20)
        self.assertEqual(len(truncated), 20)
        self.assertTrue(truncated.endswith("..."))

    def test_format_draft_job(self):
        job_item = {
            "category": "job",
            "role": "Senior Python Engineer",
            "company": "TechCorp",
            "experience": "3+ years",
            "location": "Remote",
            "skills": "Python, Django, SQLite",
            "url": "https://example.com/job/1"
        }
        draft = formatter.format_draft(job_item)
        self.assertIn("💼 Senior Python Engineer - TechCorp", draft)
        self.assertIn("📌 Experience: 3+ years", draft)
        self.assertIn("📍 Location: Remote", draft)
        self.assertIn("🎯 Skills: Python, Django, SQLite", draft)
        self.assertIn("🔗 Apply: https://example.com/job/1", draft)

    def test_format_draft_categories(self):
        categories_expected = [
            ("hackathon", "⚡"),
            ("course", "🎓"),
            ("event", "🗓️"),
            ("news", "💡"),
        ]
        for cat, icon in categories_expected:
            item = {
                "category": cat,
                "title": f"Test {cat.title()}",
                "summary": f"Great {cat} opportunity",
                "deadline": "Dec 2026",
                "location": "Global",
                "eligibility": "Students",
                "url": f"https://example.com/{cat}"
            }
            draft = formatter.format_draft(item)
            self.assertTrue(draft.startswith(icon))
            self.assertIn(f"Test {cat.title()}", draft)
            self.assertIn("Dec 2026", draft)

    def test_clean_text_short_boundaries(self):
        # Boundary cases where max_len <= 3
        self.assertEqual(formatter.clean_text("Hello World", 2), "He")
        self.assertEqual(formatter.clean_text("Hello World", 3), "Hel")
        self.assertEqual(formatter.clean_text("Hello World", 4), "H...")

    def test_format_draft_resilient_to_none_and_missing_fields(self):
        # A malformed item with None values across fields must never crash with AttributeError
        item_with_nones = {
            "category": "job",
            "company": None,
            "role": None,
            "experience": None,
            "location": None,
            "skills": None,
            "url": None,
            "title": None
        }
        draft_job = formatter.format_draft(item_with_nones)
        self.assertIn("💼 Community Update - Tech Company", draft_job)
        self.assertIn("📌 Experience: Entry / Mid level", draft_job)

        other_with_nones = {
            "category": None,
            "title": None,
            "url": None,
            "summary": None,
            "deadline": None,
            "location": None,
            "eligibility": None
        }
        draft_other = formatter.format_draft(other_with_nones)
        self.assertTrue(draft_other.startswith("💡"))
        self.assertIn("Community Update", draft_other)

    def test_get_whatsapp_target_group(self):
        self.assertEqual(formatter.get_whatsapp_target_group("hackathon"), "🏆 Hackathons")
        self.assertEqual(formatter.get_whatsapp_target_group("job"), "💼 Jobs & Internships")
        self.assertEqual(formatter.get_whatsapp_target_group("event"), "📅 Events & Workshops")
        self.assertEqual(formatter.get_whatsapp_target_group("course"), "📚 Courses and Resources")
        self.assertEqual(formatter.get_whatsapp_target_group("news"), "🌐 Tech Updates & NEWS")
        self.assertEqual(formatter.get_whatsapp_target_group("unknown"), "General Updates")
        self.assertEqual(formatter.get_whatsapp_target_group(None), "General Updates")

class TestCollectors(unittest.TestCase):
    def test_strip_html_tags(self):
        self.assertEqual(collectors.strip_html_tags(""), "")
        raw = "<style>.foo{color:red;}</style><script>alert(1);</script><!-- note --><p>Hello <b>World</b>! &amp; welcome &#39;home&#39;.</p>"
        cleaned = collectors.strip_html_tags(raw)
        self.assertEqual(cleaned, "Hello World! & welcome 'home'.")

    def test_parse_xml_feed_rss(self):
        rss_data = b"""<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0">
            <channel>
                <title>Test Feed</title>
                <item>
                    <title>Awesome Hackathon</title>
                    <link>https://example.com/hack1</link>
                    <description>&lt;p&gt;A grand hackathon for builders.&lt;/p&gt;</description>
                    <pubDate>Mon, 01 Jan 2026 00:00:00 GMT</pubDate>
                </item>
            </channel>
        </rss>"""
        entries = collectors.parse_xml_feed(rss_data)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["title"], "Awesome Hackathon")
        self.assertEqual(entries[0]["link"], "https://example.com/hack1")
        self.assertEqual(entries[0]["summary"], "A grand hackathon for builders.")
        self.assertEqual(entries[0]["date_str"], "Mon, 01 Jan 2026 00:00:00 GMT")

    def test_parse_xml_feed_atom(self):
        atom_data = b"""<?xml version="1.0" encoding="utf-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
            <title>Test Atom</title>
            <entry>
                <title>Machine Learning Paper</title>
                <link rel="alternate" href="https://example.com/ml-paper"/>
                <summary>Exploring LLM reasoning capabilities.</summary>
                <updated>2026-01-01T12:00:00Z</updated>
            </entry>
        </feed>"""
        entries = collectors.parse_xml_feed(atom_data)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["title"], "Machine Learning Paper")
        self.assertEqual(entries[0]["link"], "https://example.com/ml-paper")
        self.assertEqual(entries[0]["summary"], "Exploring LLM reasoning capabilities.")
        self.assertEqual(entries[0]["date_str"], "2026-01-01T12:00:00Z")

    def test_parse_xml_feed_malformed(self):
        malformed = b"<rss><channel><item><title>Unclosed tag"
        entries = collectors.parse_xml_feed(malformed)
        self.assertEqual(entries, [])

    @patch("collectors.fetch_data")
    def test_fetch_hacker_news_api(self, mock_fetch):
        # 1st call: topstories.json, 2nd call: item/101.json
        mock_fetch.side_effect = [
            b"[101]",
            json.dumps({
                "type": "story",
                "title": "Exciting AI Research",
                "url": "https://example.com/ai-paper",
                "score": 342,
                "by": "satya",
                "descendants": 88,
                "time": 1700000000
            }).encode("utf-8")
        ]
        items = collectors.fetch_hacker_news_api(limit=1)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "Exciting AI Research")
        self.assertEqual(items[0]["url"], "https://example.com/ai-paper")
        self.assertIn("342 points", items[0]["summary"])
        self.assertIn("88 comments", items[0]["summary"])
        self.assertIn("@satya", items[0]["summary"])

    @patch("collectors.fetch_hacker_news_api")
    @patch("collectors.fetch_data")
    def test_fetch_hacker_news_rss_cleans_comments(self, mock_fetch_data, mock_api):
        mock_api.return_value = []  # Force fallback to RSS
        hn_rss_with_comments_only = b"""<?xml version="1.0"?>
        <rss version="2.0">
            <channel>
                <item>
                    <title>New Tech Release</title>
                    <link>https://example.com/tech-release</link>
                    <description><![CDATA[<a href="https://news.ycombinator.com/item?id=123">Comments</a>]]></description>
                    <pubDate>Mon, 01 Jan 2026 00:00:00 GMT</pubDate>
                </item>
            </channel>
        </rss>"""
        mock_fetch_data.return_value = hn_rss_with_comments_only
        items = collectors.fetch_hacker_news(limit=1)
        self.assertEqual(len(items), 1)
        # Summary must NOT be "Comments"
        self.assertNotEqual(items[0]["summary"], "Comments")
        self.assertIn("New Tech Release", items[0]["summary"])

    def test_fetch_data_invalid(self):
        # Empty URL returns None without attempting network
        self.assertIsNone(collectors.fetch_data(""))
        self.assertIsNone(collectors.fetch_data(None))

class TestPipeline(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "pipeline_test.db")
        self.md_path = os.path.join(self.temp_dir.name, "PENDING_DRAFTS_TEST.md")

    def tearDown(self):
        self.temp_dir.cleanup()

    @patch("collectors.collect_all_items")
    def test_run_pipeline_end_to_end(self, mock_collect):
        mock_collect.return_value = [
            {
                "source": "Devpost",
                "category": "hackathon",
                "title": "AI Innovation Cup",
                "url": "https://devpost.com/hackathons/ai-cup",
                "summary": "Compete for $50k in prizes",
                "deadline": "March 2026",
                "location": "Online",
                "eligibility": "Open to all"
            },
            {
                "source": "WeWorkRemotely",
                "category": "job",
                "title": "Backend Engineer at CloudScale",
                "role": "Backend Engineer",
                "company": "CloudScale",
                "url": "https://weworkremotely.com/jobs/backend-engineer",
                "summary": "Scale Python microservices",
                "location": "Remote",
                "experience": "Senior",
                "skills": "Python, Docker"
            }
        ]

        summary1 = pipeline.run_pipeline(
            limit_per_feed=2,
            export_only=False,
            db_path=self.db_path,
            markdown_path=self.md_path
        )
        self.assertEqual(summary1["collected"], 2)
        self.assertEqual(summary1["new_pending"], 2)
        self.assertEqual(summary1["duplicates_skipped"], 0)
        self.assertTrue(os.path.exists(self.md_path))

        with open(self.md_path, "r", encoding="utf-8") as f:
            md_content = f.read()
            self.assertIn("AI Innovation Cup", md_content)
            self.assertIn("Backend Engineer at CloudScale", md_content)
            self.assertIn("🏆 Hackathons", md_content)
            self.assertIn("💼 Jobs & Internships", md_content)

        # Second run with same items -> should detect duplicates
        summary2 = pipeline.run_pipeline(
            limit_per_feed=2,
            export_only=False,
            db_path=self.db_path,
            markdown_path=self.md_path
        )
        self.assertEqual(summary2["collected"], 2)
        self.assertEqual(summary2["new_pending"], 0)
        self.assertEqual(summary2["duplicates_skipped"], 2)

class TestTelegramBot(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "tg_test.db")
        db.init_db(self.db_path)
        self.item_id = db.insert_item({
            "source": "Devpost",
            "category": "hackathon",
            "title": "Hackathon Alpha",
            "url": "https://devpost.com/hackathons/alpha",
            "summary": "Hackathon test",
            "draft_text": "⚡ Hackathon Alpha\n\n🔗 https://devpost.com/hackathons/alpha"
        }, self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_keyboards(self):
        kb = telegram_bot.get_approval_keyboard(42)
        self.assertIn("inline_keyboard", kb)
        buttons = kb["inline_keyboard"]
        callbacks = [btn["callback_data"] for row in buttons for btn in row]
        self.assertIn("approve:42", callbacks)
        self.assertIn("reject:42", callbacks)
        self.assertIn("done:42", callbacks)
        self.assertIn("edit:42", callbacks)

        pkb = telegram_bot.get_post_keyboard(42)
        p_callbacks = [btn["callback_data"] for row in pkb["inline_keyboard"] for btn in row]
        self.assertIn("done:42", p_callbacks)
        self.assertIn("reject:42", p_callbacks)

    def test_keyboards_with_draft_copy_text(self):
        draft_text = "⚡ Sample Hackathon Post"
        kb = telegram_bot.get_approval_keyboard(42, draft_text=draft_text)
        has_copy = any(
            btn.get("copy_text", {}).get("text") == draft_text
            for row in kb["inline_keyboard"]
            for btn in row
        )
        self.assertTrue(has_copy)

    def test_callback_approve_and_done(self):
        bot_mock = MagicMock()
        query = {
            "id": "q123",
            "data": f"approve:{self.item_id}",
            "message": {
                "message_id": 101,
                "chat": {"id": 12345},
                "text": "Card text"
            }
        }
        with patch("pipeline.export_pending_markdown"):
            telegram_bot.handle_callback_query(bot_mock, query, db_path=self.db_path)
            
        bot_mock.answer_callback_query.assert_called_once()
        bot_mock.edit_message_text.assert_called_once()
        
        # Verify DB updated
        pending = db.get_pending_items(limit=10, db_path=self.db_path)
        self.assertEqual(len(pending), 0)
        stats = db.get_stats(self.db_path)
        self.assertEqual(stats.get("approved"), 1)

        # Now test 'done'
        bot_mock.reset_mock()
        query["data"] = f"done:{self.item_id}"
        with patch("pipeline.export_pending_markdown"):
            telegram_bot.handle_callback_query(bot_mock, query, db_path=self.db_path)
            
        stats = db.get_stats(self.db_path)
        self.assertEqual(stats.get("posted"), 1)

    def test_callback_edit(self):
        bot_mock = MagicMock()
        query = {
            "id": "q_edit",
            "data": f"edit:{self.item_id}",
            "message": {
                "message_id": 105,
                "chat": {"id": 999},
                "text": "Card text"
            }
        }
        telegram_bot.handle_callback_query(bot_mock, query, db_path=self.db_path)
        bot_mock.answer_callback_query.assert_called_with("q_edit", text=f"Editing #{self.item_id}. See instructions below.", show_alert=False)
        bot_mock.send_message.assert_called_once()
        sent_msg = bot_mock.send_message.call_args[0][1]
        self.assertIn(f"/edit {self.item_id}", sent_msg)

    def test_callback_invalid_data(self):
        bot_mock = MagicMock()
        # Missing colon
        query_invalid_colon = {"id": "q1", "data": "invalid_no_colon"}
        telegram_bot.handle_callback_query(bot_mock, query_invalid_colon, db_path=self.db_path)
        bot_mock.answer_callback_query.assert_called_with("q1", text="Invalid action")

        # Non-numeric ID
        bot_mock.reset_mock()
        query_non_numeric = {"id": "q2", "data": "approve:abc"}
        telegram_bot.handle_callback_query(bot_mock, query_non_numeric, db_path=self.db_path)
        bot_mock.answer_callback_query.assert_called_with("q2", text="Invalid item ID")

    def test_text_commands(self):
        bot_mock = MagicMock()
        # Test /status
        msg_status = {"chat": {"id": 123}, "text": "/status"}
        telegram_bot.handle_text_command(bot_mock, msg_status, db_path=self.db_path)
        bot_mock.send_message.assert_called_once()
        sent_text = bot_mock.send_message.call_args[0][1]
        self.assertIn("Legion Content Queue Stats", sent_text)

        # Test /start
        bot_mock.reset_mock()
        msg_start = {"chat": {"id": 123}, "text": "/start"}
        telegram_bot.handle_text_command(bot_mock, msg_start, db_path=self.db_path)
        bot_mock.send_message.assert_called_once()

    def test_text_command_edit(self):
        bot_mock = MagicMock()
        msg_edit = {
            "chat": {"id": 123},
            "text": f"/edit {self.item_id} ⚡ Updated Draft Text By Admin"
        }
        with patch("pipeline.export_pending_markdown"):
            telegram_bot.handle_text_command(bot_mock, msg_edit, db_path=self.db_path)
            
        # Verify DB updated
        item = db.get_item(self.item_id, db_path=self.db_path)
        self.assertEqual(item["draft_text"], "⚡ Updated Draft Text By Admin")
        bot_mock.send_message.assert_called()

    def test_send_message_truncation_and_fallback(self):
        client = telegram_bot.TelegramClient("dummy_token")
        with patch.object(client, "_post") as mock_post:
            mock_post.return_value = {"ok": True}
            very_long_text = "x" * 5000
            client.send_message("123", very_long_text)
            mock_post.assert_called_once()
            called_payload = mock_post.call_args[0][1]
            self.assertTrue(len(called_payload["text"]) <= 4000)
            self.assertTrue(called_payload["text"].endswith("..."))

    def test_format_telegram_card(self):
        item = {
            "id": 99,
            "category": "hackathon",
            "source": "Devpost",
            "url": "https://devpost.com/hackathons/test",
            "draft_text": "⚡ Test Hackathon"
        }
        card = telegram_bot.format_telegram_card(item)
        self.assertIn("[Legion Review #99]", card)
        self.assertIn("HACKATHON", card)
        self.assertIn("🏆 Hackathons", card)
        self.assertIn("⚡ Test Hackathon", card)

class TestCollectorsEdgeCases(unittest.TestCase):
    @patch("collectors.fetch_data")
    def test_remoteok_json_edge_cases(self, mock_fetch):
        # 1. Non-JSON bytes
        mock_fetch.return_value = b"Not JSON"
        items = collectors.fetch_remoteok_jobs(limit=5)
        self.assertEqual(items, [])

        # 2. JSON list with invalid item structures
        mock_fetch.return_value = json.dumps([
            {"legal": "disclaimer only"},
            "not a dict",
            {"position": "DevOps Engineer", "company": "Acme", "url": "/jobs/123", "tags": ["linux", "k8s"]}
        ]).encode("utf-8")
        items = collectors.fetch_remoteok_jobs(limit=5)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["role"], "DevOps Engineer")
        self.assertEqual(items[0]["url"], "https://remoteok.com/jobs/123")
        self.assertEqual(items[0]["category"], "job")

    @patch("collectors.fetch_hyderabad_venue_events")
    @patch("collectors.fetch_devpost_hyderabad_hackathons")
    @patch("collectors.fetch_unstop_hackathons")
    @patch("collectors.fetch_meetup_hyderabad_events")
    @patch("collectors.fetch_luma_hyderabad_events")
    @patch("collectors.fetch_commudle_events")
    @patch("collectors.fetch_tech_events")
    @patch("collectors.fetch_remoteok_jobs")
    @patch("collectors.fetch_weworkremotely_jobs")
    @patch("collectors.fetch_reddit_courses")
    @patch("collectors.fetch_hacker_news")
    @patch("collectors.fetch_devpost_hackathons")
    def test_collect_all_items_fault_tolerance(
        self, mock_devpost, mock_hn, mock_reddit, mock_wwr, mock_remoteok, mock_events,
        mock_commudle, mock_luma, mock_meetup, mock_unstop, mock_devpost_hyd, mock_venue
    ):
        # If one collector throws an uncaught error, others should continue without unmocked network calls
        mock_devpost.side_effect = RuntimeError("Devpost API broke")
        mock_hn.return_value = [{"source": "HN", "category": "news", "title": "Test HN", "url": "https://news.com"}]
        mock_reddit.return_value = [{"source": "Reddit", "category": "course", "title": "ML Course", "url": "https://reddit.com/c"}]
        mock_wwr.return_value = []
        mock_remoteok.return_value = []
        mock_events.return_value = []
        mock_commudle.return_value = []
        mock_luma.return_value = []
        mock_meetup.return_value = []
        mock_unstop.return_value = []
        mock_devpost_hyd.return_value = []
        mock_venue.return_value = []
        
        items = collectors.collect_all_items(limit_per_feed=2)
        # Verify that even with RuntimeError in Devpost, HN and Reddit returned items safely
        self.assertEqual(len(items), 2)
        self.assertTrue(any(it["source"] == "HN" for it in items))
        self.assertTrue(any(it["source"] == "Reddit" for it in items))

    def test_cli_edit_flow(self):
        temp_dir = tempfile.TemporaryDirectory()
        db_path = os.path.join(temp_dir.name, "cli_test.db")
        md_path = os.path.join(temp_dir.name, "cli_drafts.md")
        db.init_db(db_path)
        item_id = db.insert_item({
            "source": "Devpost",
            "category": "hackathon",
            "title": "CLI Hackathon",
            "url": "https://example.com/cli-hack",
            "draft_text": "Original CLI Draft"
        }, db_path)

        # Simulate running main with --edit <item_id> --text "New CLI Text"
        with patch("sys.argv", ["telegram_bot.py", "--edit", str(item_id), "--text", "Updated via CLI argument", "--db", db_path]):
            with patch("config.PENDING_DRAFTS_PATH", md_path):
                telegram_bot.main()

        item = db.get_item(item_id, db_path)
        self.assertEqual(item["draft_text"], "Updated via CLI argument")
        temp_dir.cleanup()

class TestPipelineEdgeCases(unittest.TestCase):
    def test_export_empty_markdown(self):
        temp_dir = tempfile.TemporaryDirectory()
        db_path = os.path.join(temp_dir.name, "empty.db")
        md_path = os.path.join(temp_dir.name, "EMPTY.md")
        db.init_db(db_path)

        pipeline.export_pending_markdown(db_path=db_path, output_path=md_path)
        with open(md_path, "r", encoding="utf-8") as f:
            content = f.read()
            self.assertIn("All caught up! No pending drafts to review", content)

        temp_dir.cleanup()

class TestSidecarConfig(unittest.TestCase):
    def test_sidecar_json_file(self):
        sidecar_path = r"C:\Users\kunbh\.gemini\config\sidecars\legion-content-pipeline\sidecar.json"
        self.assertTrue(os.path.exists(sidecar_path), f"Sidecar config not found at {sidecar_path}")
        
        with open(sidecar_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)

        expected_config = {
            "builtin": "schedule",
            "args": [
                "CRON_TZ=Asia/Kolkata 0 */6 * * *",
                "agentapi",
                "new-conversation",
                "--",
                "Run the Legion Automation pipeline in c:\\Users\\kunbh\\Legion Automation System, collect latest opportunities across all categories, deduplicate against the SQLite database, format drafts in the Legion house style, and summarize newly pending items."
            ],
            "restart_policy": "always",
            "display_name": "Legion Content Pipeline",
            "description": "Runs every 6 hours (Asia/Kolkata) to collect, deduplicate, and draft opportunities for the Legion community.",
            "agent_permissions": {
                "workspace_uris": [
                    "file:///c:/Users/kunbh/Legion Automation System"
                ],
                "access_grants": [
                    "command(python pipeline.py)",
                    "command(python)"
                ]
            }
        }
            
        # Strict full structure equality
        self.assertEqual(cfg, expected_config)
        self.assertEqual(cfg.get("builtin"), "schedule")
        self.assertEqual(cfg.get("restart_policy"), "always")
        self.assertEqual(cfg.get("display_name"), "Legion Content Pipeline")
        self.assertEqual(cfg.get("description"), expected_config["description"])
        
        args = cfg.get("args", [])
        self.assertEqual(len(args), 5)
        self.assertEqual(args[0], "CRON_TZ=Asia/Kolkata 0 */6 * * *")
        self.assertEqual(args[1], "agentapi")
        self.assertEqual(args[2], "new-conversation")
        self.assertEqual(args[3], "--")
        self.assertEqual(args[4], expected_config["args"][4])
        
        perms = cfg.get("agent_permissions")
        self.assertIsNotNone(perms)
        self.assertIn("workspace_uris", perms)
        self.assertEqual(perms["workspace_uris"], ["file:///c:/Users/kunbh/Legion Automation System"])
        self.assertIn("access_grants", perms)
        self.assertEqual(perms["access_grants"], ["command(python pipeline.py)", "command(python)"])
        self.assertNotIn("allow", perms)
        self.assertNotIn("allow", cfg)

    def test_sidecar_permissions_security_rules(self):
        sidecar_path = r"C:\Users\kunbh\.gemini\config\sidecars\legion-content-pipeline\sidecar.json"
        with open(sidecar_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)

        perms = cfg.get("agent_permissions", {})
        # Verify no wildcards in access grants
        for grant in perms.get("access_grants", []):
            self.assertNotIn("*", grant, f"Overly permissive wildcard grant found: {grant}")
            self.assertTrue(grant.startswith("command("), f"Grant does not start with command(...): {grant}")

        # Verify workspace URI structure and directory existence
        for uri in perms.get("workspace_uris", []):
            self.assertTrue(uri.startswith("file:///"), f"Workspace URI missing file:/// scheme: {uri}")
            path = uri.replace("file:///", "").replace("/", os.sep)
            self.assertTrue(os.path.exists(path), f"Workspace URI path does not exist on disk: {path}")

class TestLegionFeedIntegration(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_feed.db")
        self.html_path = os.path.join(self.temp_dir.name, "test_feed.html")
        db.init_db(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_parse_legion_feed_html(self):
        sample_html = """<!DOCTYPE html>
        <html><body>
        <script>
        var SEED=[
        {id:"s1",type:"hackathon",title:"HackGlobal",sum:"Global hackathon",date:"2026-11-14",where:"Online",src:"HackCulture",link:"https://hackculture.io",posted:"2026-11-06"},
        {id:"s9",type:"job",title:"Backend Engineer at CloudScale",sum:"Python microservices",date:"2026-11-20",where:"Remote",src:"Admin",link:"",posted:"2026-11-07"}
        ];
        </script>
        </body></html>"""
        items = collectors.parse_legion_feed_html(sample_html)
        self.assertEqual(len(items), 2)
        
        # Test hackathon parsing
        self.assertEqual(items[0]["category"], "hackathon")
        self.assertEqual(items[0]["title"], "HackGlobal")
        self.assertEqual(items[0]["url"], "https://hackculture.io")
        self.assertEqual(items[0]["location"], "Online")
        self.assertEqual(items[0]["source"], "HackCulture")

        # Test job role & company extraction and synthetic canonical URL
        self.assertEqual(items[1]["category"], "job")
        self.assertEqual(items[1]["role"], "Backend Engineer")
        self.assertEqual(items[1]["company"], "CloudScale")
        self.assertEqual(items[1]["url"], "https://legion.community/feed/s9")

    def test_parse_legion_feed_html_empty_and_malformed(self):
        self.assertEqual(collectors.parse_legion_feed_html(""), [])
        self.assertEqual(collectors.parse_legion_feed_html(None), [])
        self.assertEqual(collectors.parse_legion_feed_html("<html>No seed variable</html>"), [])

    def test_fetch_legion_feed_items(self):
        sample_html = """<script>var SEED=[{id:"t1",type:"course",title:"AI Bootcamp",sum:"Learn AI",date:"",where:"Online",src:"Edu",link:"https://example.com/ai",posted:"2026-11-07"}];</script>"""
        with open(self.html_path, "w", encoding="utf-8") as f:
            f.write(sample_html)
            
        items = collectors.fetch_legion_feed_items(html_path=self.html_path)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "AI Bootcamp")

    def test_fetch_legion_feed_items_fallback_to_curated(self):
        # Point to non-existent file path
        items = collectors.fetch_legion_feed_items(html_path="non_existent_file.html")
        self.assertTrue(len(items) > 0)
        self.assertEqual(items[0]["title"], collectors.CURATED_SEED_ITEMS[0]["title"])

    def test_import_legion_feed(self):
        sample_html = """<script>var SEED=[
        {id:"imp1",type:"event",title:"Tech Summit 2026",sum:"AI summit",date:"2026-12-01",where:"Hyderabad",src:"Luma",link:"https://luma.com/summit",posted:"2026-11-07"}
        ];</script>"""
        with open(self.html_path, "w", encoding="utf-8") as f:
            f.write(sample_html)

        imported1 = pipeline.import_legion_feed(html_path=self.html_path, db_path=self.db_path)
        self.assertEqual(imported1, 1)

        # Verify DB item created with house-style draft
        pending = db.get_pending_items(limit=10, db_path=self.db_path)
        self.assertEqual(len(pending), 1)
        self.assertIn("Tech Summit 2026", pending[0]["draft_text"])
        self.assertTrue(pending[0]["draft_text"].startswith("🗓️"))

        # Deduplication check on second run
        imported2 = pipeline.import_legion_feed(html_path=self.html_path, db_path=self.db_path)
        self.assertEqual(imported2, 0)

    def test_export_feed_html(self):
        # Insert test item into DB
        db.insert_item({
            "source": "Devpost",
            "category": "hackathon",
            "title": "Quantum Hackathon",
            "url": "https://devpost.com/hackathons/quantum",
            "summary": "Build quantum algorithms",
            "deadline": "2026-12-15",
            "location": "Online"
        }, self.db_path)

        base_template = """<!DOCTYPE html><html><body><script>
        var SEED=[{id:"s1",type:"job",title:"Old Job",sum:"Old job",date:"",where:"Remote",src:"Admin",link:"https://example.com/job",posted:"2026-11-01"}];
        var TODAY="2026-11-01";
        </script></body></html>"""
        with open(self.html_path, "w", encoding="utf-8") as f:
            f.write(base_template)

        out_html_path = os.path.join(self.temp_dir.name, "exported_feed.html")
        exported = pipeline.export_feed_html(
            db_path=self.db_path,
            template_path=self.html_path,
            output_path=out_html_path
        )
        self.assertTrue(os.path.exists(out_html_path))
        self.assertIn("Quantum Hackathon", exported)
        self.assertIn("Old Job", exported)

        # Verify SEED array in exported HTML is valid JSON
        import re
        m = re.search(r'var\s+SEED\s*=\s*(\[[\s\S]*?\])\s*;', exported)
        self.assertIsNotNone(m)
        parsed_seed = json.loads(m.group(1))
        titles = [x["title"] for x in parsed_seed]
        self.assertIn("Quantum Hackathon", titles)
        self.assertIn("Old Job", titles)

    def test_telegram_bot_feed_command(self):
        bot_mock = MagicMock()
        msg_feed = {"chat": {"id": 456}, "text": "/feed"}
        telegram_bot.handle_text_command(bot_mock, msg_feed, db_path=self.db_path)
        bot_mock.send_message.assert_called_once()
        sent_text = bot_mock.send_message.call_args[0][1]
        self.assertIn("Legion Feed Web Dashboard", sent_text)

        bot_mock.reset_mock()
        msg_web = {"chat": {"id": 456}, "text": "/web"}
        telegram_bot.handle_text_command(bot_mock, msg_web, db_path=self.db_path)
        bot_mock.send_message.assert_called_once()
        sent_text2 = bot_mock.send_message.call_args[0][1]
        self.assertIn("Legion Feed Web Dashboard", sent_text2)

    @patch("webbrowser.open")
    def test_open_feed_html(self, mock_web_open):
        with open(self.html_path, "w", encoding="utf-8") as f:
            f.write("<html><body>Test Feed</body></html>")
            
        success = pipeline.open_feed_html(html_path=self.html_path)
        self.assertTrue(success)
        mock_web_open.assert_called_once()
        called_url = mock_web_open.call_args[0][0]
        self.assertTrue(called_url.startswith("file:///"))


class TestHyderabadVenueDetection(unittest.TestCase):
    def test_detect_thub_variants(self):
        self.assertEqual(formatter.detect_hyderabad_venue("T-Hub Phase 2, Knowledge City"), "T-Hub Phase 2")
        self.assertEqual(formatter.detect_hyderabad_venue("Event at THub 2.0"), "T-Hub Phase 2")
        self.assertEqual(formatter.detect_hyderabad_venue("Networking at T-Hub"), "T-Hub")
        self.assertEqual(formatter.detect_hyderabad_venue("Workshop at T-Hub Phase 2"), "T-Hub Phase 2")
        self.assertEqual(formatter.detect_hyderabad_venue("Conference at T-Hub Phase II"), "T-Hub Phase 2")
        self.assertEqual(formatter.detect_hyderabad_venue("Symposium at T-Hub, Phase 2"), "T-Hub Phase 2")
        self.assertEqual(formatter.detect_hyderabad_venue("Hackathon at T-Hub-Phase-2"), "T-Hub Phase 2")

    def test_detect_iiit_hyderabad_variants(self):
        self.assertEqual(formatter.detect_hyderabad_venue("Hackathon at IIIT Hyderabad"), "IIIT Hyderabad")
        self.assertEqual(formatter.detect_hyderabad_venue("CIE, IIIT-H Campus"), "IIIT Hyderabad")
        self.assertEqual(formatter.detect_hyderabad_venue("Research talk at IIITH"), "IIIT Hyderabad")
        self.assertEqual(formatter.detect_hyderabad_venue("Workshop at International Institute of Information Technology Hyderabad"), "IIIT Hyderabad")

    def test_detect_wework_variants(self):
        self.assertEqual(formatter.detect_hyderabad_venue("WeWork Krishe Emerald Kondapur"), "WeWork Kondapur")
        self.assertEqual(formatter.detect_hyderabad_venue("WeWork Rajapushpa Summit Nanakramguda"), "WeWork Nanakramguda")
        self.assertEqual(formatter.detect_hyderabad_venue("WeWork Hyderabad Coworking"), "WeWork")

    def test_detect_tech_hubs_and_companies(self):
        self.assertEqual(formatter.detect_hyderabad_venue("DevX, Hitec City"), "DevX")
        self.assertEqual(formatter.detect_hyderabad_venue("Hardware prototyping at T-Works"), "T-Works")
        self.assertEqual(formatter.detect_hyderabad_venue("Meetup at People Tech"), "People Tech")
        self.assertEqual(formatter.detect_hyderabad_venue("Conference at EPAM Hyderabad"), "EPAM")
        self.assertEqual(formatter.detect_hyderabad_venue("College hackathon at CBIT"), "CBIT")
        self.assertEqual(formatter.detect_hyderabad_venue("Global DevFest at HICC"), "HICC")
        self.assertEqual(formatter.detect_hyderabad_venue("Expo at HITEX Exhibition Centre"), "HITEX")
        self.assertEqual(formatter.detect_hyderabad_venue("Tech talks at Mindspace Madhapur"), "Mindspace Madhapur")
        self.assertEqual(formatter.detect_hyderabad_venue("Office at Salarpuria Sattva Knowledge City"), "Knowledge City")
        self.assertEqual(formatter.detect_hyderabad_venue("Cyber Towers, Hitec City"), "Cyber Towers")

    def test_detect_neighborhood_hubs(self):
        self.assertEqual(formatter.detect_hyderabad_venue("Office in Gachibowli, Hyderabad"), "Gachibowli")
        self.assertEqual(formatter.detect_hyderabad_venue("Cafe in Kondapur"), "Kondapur")
        self.assertEqual(formatter.detect_hyderabad_venue("Coworking in Madhapur"), "Madhapur")
        self.assertEqual(formatter.detect_hyderabad_venue("WaveRock, Nanakramguda"), "Nanakramguda")
        self.assertEqual(formatter.detect_hyderabad_venue("Tower at Financial District"), "Financial District")
        self.assertEqual(formatter.detect_hyderabad_venue("Banjara Hills Road 12"), "Banjara Hills")
        self.assertEqual(formatter.detect_hyderabad_venue("Jubilee Hills Checkpost"), "Jubilee Hills")

    def test_detect_venue_empty_and_unrelated(self):
        self.assertIsNone(formatter.detect_hyderabad_venue(""))
        self.assertIsNone(formatter.detect_hyderabad_venue(None))
        self.assertIsNone(formatter.detect_hyderabad_venue("San Francisco, CA"))
        self.assertIsNone(formatter.detect_hyderabad_venue("Whitefield, Bangalore"))


class TestHyderabadLocationRouting(unittest.TestCase):
    def test_is_hyderabad_offline(self):
        # Hyderabad physical location
        self.assertTrue(formatter.is_hyderabad_offline({
            "title": "GDG Hyderabad Annual DevFest",
            "location": "Hyderabad",
            "summary": "Join in-person tech community gathering."
        }))

        # Venue without explicit 'Hyderabad' in location string
        self.assertTrue(formatter.is_hyderabad_offline({
            "title": "Startup Pitch Night",
            "location": "T-Hub Phase 2, Gachibowli",
            "summary": "Pitch your AI startup."
        }))

        # Explicit online event even with 'Hyderabad' in community name -> False
        self.assertFalse(formatter.is_hyderabad_offline({
            "title": "GDG Hyderabad Virtual Webinar",
            "location": "Online (Zoom)",
            "summary": "Join our virtual Zoom session from home."
        }))

        # Online event discussing venues in summary must NOT be marked offline
        self.assertFalse(formatter.is_hyderabad_offline({
            "title": "GDG Hyderabad Online Meet",
            "location": "Online",
            "summary": "Discussing venue selection for our upcoming annual developer conference."
        }))
        self.assertFalse(formatter.is_hyderabad_offline({
            "title": "HydPy Virtual Python Concurrency",
            "location": "Online (Google Meet)",
            "summary": "Online webinar. Note: No offline venue for this edition."
        }))

        # Hybrid event with in-person venue -> True
        self.assertTrue(formatter.is_hyderabad_offline({
            "title": "GDG Hyderabad Hybrid DevFest",
            "location": "Online & T-Hub Phase 2",
            "summary": "Attend in person at T-Hub or watch the virtual livestream."
        }))

        # Non-Hyderabad offline event
        self.assertFalse(formatter.is_hyderabad_offline({
            "title": "Bangalore PyData Meetup",
            "location": "Koramangala, Bangalore",
            "summary": "In-person at Microsoft Reactor."
        }))

    def test_is_location_allowed(self):
        # 1. Hyderabad offline -> allowed
        self.assertTrue(formatter.is_location_allowed({
            "title": "React Hyderabad Meetup",
            "location": "DevX, Hitec City",
            "summary": "In-person React workshop."
        }))

        # 2. Virtual / Online / Remote -> allowed
        self.assertTrue(formatter.is_location_allowed({
            "title": "Global AI Sprint",
            "location": "Online",
            "summary": "Worldwide virtual hackathon."
        }))
        self.assertTrue(formatter.is_location_allowed({
            "title": "Remote Python Role",
            "location": "Remote",
            "summary": "Work from anywhere in India."
        }))

        # 3. Disallowed other offline cities -> discarded
        self.assertFalse(formatter.is_location_allowed({
            "title": "Bangalore DevOps Summit",
            "location": "Bangalore",
            "summary": "In-person conference at Chancery Pavilion."
        }))
        self.assertFalse(formatter.is_location_allowed({
            "title": "Delhi Tech Mixer",
            "location": "New Delhi, Connaught Place",
            "summary": "Offline networking mixer."
        }))
        self.assertFalse(formatter.is_location_allowed({
            "title": "Mumbai FinTech Workshop",
            "location": "BKC, Mumbai",
            "summary": "In-person workshop."
        }))
        self.assertFalse(formatter.is_location_allowed({
            "title": "Pune Cloud Community",
            "location": "Pune, Maharashtra",
            "summary": "In-person meetup at Hinjawadi."
        }))

        # 4. Other city explicitly in title (e.g. "Bangalore React Conference", "Mumbai AI Hackathon") -> discarded
        self.assertFalse(formatter.is_location_allowed({
            "title": "Bangalore React Conference",
            "location": "Convention Center",
            "summary": "Two days of talks and networking."
        }))
        self.assertFalse(formatter.is_location_allowed({
            "title": "Mumbai AI Hackathon",
            "location": "IIT Bombay Campus",
            "summary": "In-person student hackathon."
        }))
        self.assertFalse(formatter.is_location_allowed({
            "title": "Pune Cloud Meetup",
            "location": "Auditorium B",
            "summary": "Cloud architecture talks."
        }))

        # 5. Other city event with explicit virtual/online stream -> allowed
        self.assertTrue(formatter.is_location_allowed({
            "title": "Bangalore React Conference (Virtual Livestream)",
            "location": "Bangalore",
            "summary": "Streamed live across India via YouTube livestream."
        }))

        # 6. National / Pan-India competition -> allowed
        self.assertTrue(formatter.is_location_allowed({
            "title": "Smart India Hackathon 2026",
            "location": "Pan-India",
            "summary": "Open to college teams across India with remote phases."
        }))


class TestHyderabadBadgesAndFormatting(unittest.TestCase):
    def test_get_location_badge(self):
        hyd_item = {
            "title": "HydPy Monthly Meetup",
            "location": "IIIT Hyderabad, Gachibowli",
            "summary": "Python talks and networking."
        }
        self.assertEqual(formatter.get_location_badge(hyd_item), "[HYDERABAD - OFFLINE]")

        online_item = {
            "title": "Intro to PyTorch",
            "location": "Online",
            "summary": "Virtual webinar."
        }
        self.assertEqual(formatter.get_location_badge(online_item), "[ONLINE]")

    def test_format_location_string(self):
        # Hyderabad with venue
        hyd_with_venue = {
            "title": "AWS User Group Meetup",
            "location": "T-Hub Phase 2, Gachibowli",
            "summary": "Serverless architectures in-person."
        }
        self.assertEqual(
            formatter.format_location_string(hyd_with_venue),
            "[HYDERABAD - OFFLINE] Hyderabad (T-Hub Phase 2)"
        )

        # Hyderabad without specific venue
        hyd_generic = {
            "title": "Tech Walk-in",
            "location": "Hyderabad",
            "summary": "Walk-in interview."
        }
        self.assertEqual(
            formatter.format_location_string(hyd_generic),
            "[HYDERABAD - OFFLINE] Hyderabad"
        )

        # Virtual / Online
        online_item = {
            "title": "Global Hackathon",
            "location": "Online",
            "summary": "Virtual contest."
        }
        self.assertEqual(
            formatter.format_location_string(online_item),
            "[ONLINE] Online"
        )

    def test_get_location_tag(self):
        hyd_item = {
            "title": "HydPy Meetup",
            "location": "T-Hub Phase 2, Gachibowli",
            "summary": "Python meetup."
        }
        self.assertEqual(
            formatter.get_location_tag(hyd_item),
            "📍 Location: [HYDERABAD - OFFLINE] Hyderabad (T-Hub Phase 2)"
        )

        online_item = {
            "title": "Webinar",
            "location": "Online",
            "summary": "Virtual event."
        }
        self.assertEqual(
            formatter.get_location_tag(online_item),
            "📍 Location: [ONLINE] Online"
        )

    def test_format_draft_hyderabad_offline(self):
        item = {
            "category": "event",
            "title": "HydPy February Meetup",
            "summary": "Deep dive into Python 3.13 and GIL-free concurrency.",
            "date_str": "Feb 28, 2026",
            "deadline": "Feb 27, 2026",
            "location": "T-Hub Phase 2, Gachibowli",
            "eligibility": "Python enthusiasts and software engineers",
            "url": "https://www.commudle.com/events/hydpy-feb"
        }
        draft = formatter.format_draft(item)
        self.assertIn("[HYDERABAD - OFFLINE]", draft)
        self.assertIn("📍 Where: [HYDERABAD - OFFLINE] Hyderabad (T-Hub Phase 2)", draft)
        self.assertTrue(draft.startswith("🗓️"))
        self.assertIn("Feb 27, 2026", draft)
        self.assertIn("https://www.commudle.com/events/hydpy-feb", draft)

    def test_format_draft_virtual_event(self):
        item = {
            "category": "event",
            "title": "Global Cloud Architecture Summit",
            "summary": "Learn Kubernetes and microservices best practices.",
            "date_str": "March 15, 2026",
            "location": "Online",
            "eligibility": "Cloud architects and DevOps engineers",
            "url": "https://example.com/cloud-summit"
        }
        draft = formatter.format_draft(item)
        self.assertIn("[ONLINE]", draft)
        self.assertIn("📍 Where: [ONLINE] Online", draft)
        self.assertTrue(draft.startswith("🗓️"))


class TestHyderabadCollectors(unittest.TestCase):
    @patch("collectors.fetch_data")
    def test_fetch_commudle_events_json(self, mock_fetch):
        mock_payload = {
            "data": [
                {
                    "title": "GDG Hyderabad - Flutter Forward Extended",
                    "slug": "gdg-flutter-forward",
                    "description": "<p>Deep dive into Flutter mobile development at T-Hub Phase 2.</p>",
                    "start_date": "2026-03-10",
                    "venue": {"name": "T-Hub Phase 2", "city": "Hyderabad"},
                    "community": {"name": "GDG Hyderabad"}
                }
            ]
        }
        mock_fetch.return_value = json.dumps(mock_payload).encode("utf-8")
        items = collectors.fetch_commudle_events(limit=5)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "GDG Hyderabad - Flutter Forward Extended")
        self.assertEqual(items[0]["url"], "https://www.commudle.com/events/gdg-flutter-forward")
        self.assertIn("T-Hub Phase 2", items[0]["location"])
        self.assertEqual(items[0]["source"], "Commudle (GDG Hyderabad)")
        self.assertEqual(items[0]["category"], "event")

    @patch("collectors.fetch_data")
    def test_fetch_commudle_events_html_fallback(self, mock_fetch):
        # 1st call (primary JSON API) returns None, 2nd call (fallback HTML) returns HTML
        html_fallback = b"""<!DOCTYPE html>
        <html><body>
        <div class="event-card">
            <a href="/events/hydpy-python-deep-dive">
                <h3>HydPy Python Deep Dive</h3>
            </a>
        </div>
        </body></html>"""
        mock_fetch.side_effect = [None, html_fallback]
        items = collectors.fetch_commudle_events(limit=5)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "HydPy Python Deep Dive")
        self.assertEqual(items[0]["url"], "https://www.commudle.com/events/hydpy-python-deep-dive")
        self.assertEqual(items[0]["location"], "Hyderabad")

    @patch("collectors.fetch_data")
    def test_fetch_luma_hyderabad_events_json(self, mock_fetch):
        mock_payload = {
            "events": [
                {
                    "name": "AI Engineers Hyderabad Meetup",
                    "url_slug": "ai-engineers-hyd",
                    "description": "Building LLM agents with LangChain.",
                    "start_at": "2026-03-20",
                    "geo_address_info": "IIIT Hyderabad, Gachibowli"
                }
            ]
        }
        mock_fetch.return_value = json.dumps(mock_payload).encode("utf-8")
        items = collectors.fetch_luma_hyderabad_events(limit=5)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "AI Engineers Hyderabad Meetup")
        self.assertEqual(items[0]["url"], "https://lu.ma/ai-engineers-hyd")
        self.assertIn("IIIT Hyderabad", items[0]["location"])
        self.assertEqual(items[0]["source"], "Luma Hyderabad")

    @patch("collectors.fetch_data")
    def test_fetch_luma_hyderabad_fallback_next_data(self, mock_fetch):
        # Primary JSON returns empty, fallback HTML contains __NEXT_DATA__
        next_json = {
            "props": {
                "pageProps": {
                    "events": [
                        {
                            "name": "Founders & Builders Hyderabad",
                            "url": "https://lu.ma/founders-hyd",
                            "description": "Tech founders gathering in Hyderabad.",
                            "location": "WeWork Kondapur, Hyderabad",
                            "start_at": "2026-04-01"
                        }
                    ]
                }
            }
        }
        html_doc = f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps(next_json)}</script></html>'.encode("utf-8")
        mock_fetch.side_effect = [None, html_doc]
        items = collectors.fetch_luma_hyderabad_events(limit=5)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "Founders & Builders Hyderabad")
        self.assertEqual(items[0]["url"], "https://lu.ma/founders-hyd")

    @patch("collectors.fetch_data")
    def test_fetch_luma_hyderabad_events_json_ld_fallback(self, mock_fetch):
        # Primary returns None, fallback HTML has JSON-LD
        html_doc = b"""<html><head>
        <script type="application/ld+json">
        {
            "@context": "https://schema.org",
            "@type": "Event",
            "name": "GenAI Builders Meetup Hyderabad",
            "url": "https://lu.ma/genai-hyd",
            "description": "Hands-on generative AI session in Hyderabad",
            "startDate": "2026-03-25",
            "location": {"name": "T-Hub Phase 2, Hyderabad"}
        }
        </script>
        </head><body></body></html>"""
        mock_fetch.side_effect = [None, html_doc]
        items = collectors.fetch_luma_hyderabad_events(limit=5)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "GenAI Builders Meetup Hyderabad")
        self.assertEqual(items[0]["url"], "https://lu.ma/genai-hyd")
        self.assertIn("T-Hub Phase 2", items[0]["location"])

    @patch("collectors.fetch_data")
    def test_fetch_luma_hyderabad_events_html_anchor_fallback(self, mock_fetch):
        # Primary returns None, fallback HTML has anchor tags
        html_doc = b"""<html><body>
        <div class="events">
            <a href="/hyd-founders-dinner">
                <h2>Hyderabad Founders Dinner</h2>
            </a>
        </div>
        </body></html>"""
        mock_fetch.side_effect = [None, html_doc]
        items = collectors.fetch_luma_hyderabad_events(limit=5)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "Hyderabad Founders Dinner")
        self.assertEqual(items[0]["url"], "https://lu.ma/hyd-founders-dinner")

    @patch("collectors.fetch_data")
    def test_fetch_meetup_hyderabad_events_json_ld(self, mock_fetch):
        html_ld = b"""<!DOCTYPE html><html><head>
        <script type="application/ld+json">
        {
            "@type": "Event",
            "name": "React Hyderabad Meetup #45",
            "url": "https://www.meetup.com/react-hyderabad/events/123456/",
            "description": "React Server Components and Next.js 15",
            "startDate": "2026-03-14T10:00:00+05:30",
            "location": {"name": "DevX, Hitec City"}
        }
        </script>
        </head><body></body></html>"""
        mock_fetch.return_value = html_ld
        items = collectors.fetch_meetup_hyderabad_events(limit=5)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "React Hyderabad Meetup #45")
        self.assertEqual(items[0]["url"], "https://www.meetup.com/react-hyderabad/events/123456/")
        self.assertIn("DevX", items[0]["location"])

    @patch("collectors.fetch_data")
    def test_fetch_meetup_hyderabad_events_json_ld_graph(self, mock_fetch):
        # Modern CMS / Schema.org @graph structure
        html_ld_graph = b"""<!DOCTYPE html><html><head>
        <script type="application/ld+json">
        {
            "@context": "https://schema.org",
            "@graph": [
                {
                    "@type": "Organization",
                    "name": "Meetup"
                },
                {
                    "@type": "Event",
                    "name": "Hyderabad Machine Learning Study Group",
                    "url": "https://www.meetup.com/hyd-ml/events/555/",
                    "description": "Discussion on LLM reasoning and agent benchmarks.",
                    "startDate": "2026-04-10T10:00:00+05:30",
                    "location": {"name": "IIIT Hyderabad, Gachibowli"}
                }
            ]
        }
        </script>
        </head><body></body></html>"""
        mock_fetch.return_value = html_ld_graph
        items = collectors.fetch_meetup_hyderabad_events(limit=5)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "Hyderabad Machine Learning Study Group")
        self.assertEqual(items[0]["url"], "https://www.meetup.com/hyd-ml/events/555/")
        self.assertIn("IIIT Hyderabad", items[0]["location"])

    @patch("collectors.fetch_data")
    def test_fetch_meetup_hyderabad_events_rss(self, mock_fetch):
        rss_bytes = b"""<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0">
            <channel>
                <title>Meetup Hyderabad Tech</title>
                <item>
                    <title>Machine Learning Developers Hyderabad</title>
                    <link>https://www.meetup.com/mld-hyd/events/999/</link>
                    <description>Discussing transformer models and fine-tuning.</description>
                    <pubDate>Sun, 15 Mar 2026 10:00:00 GMT</pubDate>
                </item>
            </channel>
        </rss>"""
        mock_fetch.return_value = rss_bytes
        items = collectors.fetch_meetup_hyderabad_events(limit=5)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "Machine Learning Developers Hyderabad")
        self.assertEqual(items[0]["url"], "https://www.meetup.com/mld-hyd/events/999/")

    @patch("collectors.fetch_data")
    def test_fetch_unstop_hackathons(self, mock_fetch):
        mock_unstop_data = {
            "data": {
                "data": [
                    {
                        "title": "National GenAI Hackathon 2026",
                        "public_url": "https://unstop.com/hackathons/national-genai-2026",
                        "details": "Build intelligent generative agents.",
                        "start_date": "2026-03-01",
                        "end_reg_date": "2026-02-28",
                        "region": "Hyderabad / Pan-India",
                        "organisation": {"name": "IIT / IIIT Consortium"}
                    }
                ]
            }
        }
        mock_fetch.return_value = json.dumps(mock_unstop_data).encode("utf-8")
        items = collectors.fetch_unstop_hackathons(limit=5)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "National GenAI Hackathon 2026")
        self.assertEqual(items[0]["category"], "hackathon")
        self.assertEqual(items[0]["source"], "Unstop Hackathons")

    @patch("collectors.fetch_data")
    def test_fetch_devpost_hyderabad_hackathons(self, mock_fetch):
        html_devpost = b"""<!DOCTYPE html><html><body>
        <div class="hackathons-container">
            <a class="clearfix" href="https://iiit-hack.devpost.com/">
                <h5>IIIT Hyderabad Megathon 2026</h5>
            </a>
        </div>
        </body></html>"""
        mock_fetch.return_value = html_devpost
        items = collectors.fetch_devpost_hyderabad_hackathons(limit=5)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "IIIT Hyderabad Megathon 2026")
        self.assertEqual(items[0]["url"], "https://iiit-hack.devpost.com/")
        self.assertEqual(items[0]["category"], "hackathon")

    @patch("collectors.fetch_data")
    def test_fetch_hyderabad_venue_events(self, mock_fetch):
        t_hub_html = b"""<!DOCTYPE html><html><body>
        <h2>AI Acceleration Workshop at T-Hub Phase 2</h2>
        <h2>DeepTech Mentorship Session</h2>
        </body></html>"""
        mock_fetch.side_effect = [t_hub_html, None, None, None, None]
        items = collectors.fetch_hyderabad_venue_events(limit=5)
        self.assertTrue(len(items) >= 1)
        self.assertEqual(items[0]["title"], "AI Acceleration Workshop at T-Hub Phase 2")
        self.assertEqual(items[0]["source"], "T-Hub Monitor")
        self.assertIn("T-Hub Phase 2", items[0]["location"])

    @patch("collectors.fetch_data")
    def test_fetch_hyderabad_venue_events_wework_nanakramguda(self, mock_fetch):
        wework_html = b"""<!DOCTYPE html><html><body>
        <h2>Product Founders Meetup at WeWork Rajapushpa Summit</h2>
        </body></html>"""
        mock_fetch.side_effect = [None, None, None, None, wework_html]
        items = collectors.fetch_hyderabad_venue_events(limit=5)
        self.assertTrue(len(items) >= 1)
        self.assertEqual(items[0]["title"], "Product Founders Meetup at WeWork Rajapushpa Summit")
        self.assertEqual(items[0]["source"], "WeWork Nanakramguda Monitor")
        self.assertIn("WeWork Nanakramguda", items[0]["location"])

    @patch("collectors.fetch_hyderabad_venue_events")
    @patch("collectors.fetch_devpost_hyderabad_hackathons")
    @patch("collectors.fetch_unstop_hackathons")
    @patch("collectors.fetch_meetup_hyderabad_events")
    @patch("collectors.fetch_luma_hyderabad_events")
    @patch("collectors.fetch_commudle_events")
    def test_collect_hyderabad_items_resilience(
        self, mock_commudle, mock_luma, mock_meetup, mock_unstop, mock_devpost, mock_venue
    ):
        mock_commudle.side_effect = RuntimeError("Commudle API outage")
        mock_luma.return_value = [{"source": "Luma Hyderabad", "category": "event", "title": "Luma Event", "url": "https://lu.ma/1"}]
        mock_meetup.return_value = [{"source": "Meetup Hyderabad", "category": "event", "title": "Meetup Event", "url": "https://meetup.com/1"}]
        mock_unstop.return_value = []
        mock_devpost.return_value = []
        mock_venue.return_value = []

        items = collectors.collect_hyderabad_items(limit_per_feed=5)
        # Even with RuntimeError on Commudle, Luma and Meetup items collected safely
        self.assertEqual(len(items), 2)
        sources = [it["source"] for it in items]
        self.assertIn("Luma Hyderabad", sources)
        self.assertIn("Meetup Hyderabad", sources)


class TestHyderabadPipelineIntegration(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "hyd_pipeline_test.db")
        self.md_path = os.path.join(self.temp_dir.name, "HYD_PENDING.md")
        self.html_path = os.path.join(self.temp_dir.name, "hyd_feed.html")

    def tearDown(self):
        self.temp_dir.cleanup()

    @patch("collectors.collect_all_items")
    def test_pipeline_filters_disallowed_cities_and_badges_hyderabad(self, mock_collect):
        mock_collect.return_value = [
            # 1. Valid Hyderabad in-person event -> should be retained and badged
            {
                "source": "Commudle (GDG Hyderabad)",
                "category": "event",
                "title": "GDG Hyderabad DevFest",
                "url": "https://commudle.com/events/gdg-devfest",
                "summary": "Full day in-person developer festival at T-Hub Phase 2.",
                "deadline": "2026-03-15",
                "location": "T-Hub Phase 2, Gachibowli",
                "eligibility": "Developers across Hyderabad"
            },
            # 2. Disallowed offline city (Bangalore) -> should be discarded
            {
                "source": "Bangalore Feed",
                "category": "event",
                "title": "Bangalore React In-Person",
                "url": "https://example.com/bangalore-react",
                "summary": "In-person meetup at Koramangala Bangalore office.",
                "deadline": "2026-03-20",
                "location": "Bangalore",
                "eligibility": "Bangalore engineers"
            },
            # 3. Disallowed city (Delhi) with online participation -> should be retained
            {
                "source": "Delhi Feed",
                "category": "event",
                "title": "Delhi Open Source Summit (Virtual Livestream)",
                "url": "https://example.com/delhi-virtual",
                "summary": "Join live on YouTube livestream across India.",
                "deadline": "2026-03-22",
                "location": "Delhi / Online Livestream",
                "eligibility": "All developers"
            }
        ]

        summary = pipeline.run_pipeline(
            limit_per_feed=5,
            export_only=False,
            db_path=self.db_path,
            markdown_path=self.md_path,
            html_path=self.html_path,
            include_hyderabad=True
        )

        self.assertEqual(summary["collected"], 3)
        self.assertEqual(summary["new_pending"], 2)  # GDG Hyderabad + Delhi Virtual
        self.assertEqual(summary["discarded_other_cities"], 1)  # Bangalore In-Person discarded

        # Verify Markdown review file content
        self.assertTrue(os.path.exists(self.md_path))
        with open(self.md_path, "r", encoding="utf-8") as f:
            md_content = f.read()

        # GDG Hyderabad must have badge and venue
        self.assertIn("[HYDERABAD - OFFLINE]", md_content)
        self.assertIn("GDG Hyderabad DevFest", md_content)
        self.assertIn("📍 Where: [HYDERABAD - OFFLINE] Hyderabad (T-Hub Phase 2)", md_content)
        
        # Bangalore in-person event must NOT be present
        self.assertNotIn("Bangalore React In-Person", md_content)

        # Delhi virtual event must have [ONLINE] badge
        self.assertIn("Delhi Open Source Summit", md_content)
        self.assertIn("[ONLINE]", md_content)

    def test_disallowed_city_in_organizer_name_retained_for_hyderabad_event(self):
        # Event organized by a company from Bangalore but held at T-Hub Hyderabad
        item = {
            "source": "Bangalore Tech Corp",
            "category": "event",
            "title": "Bangalore AI Labs Meetup in Hyderabad",
            "url": "https://example.com/hyd-meetup",
            "summary": "Bangalore AI Labs travels to Hyderabad for an in-person workshop at T-Hub Phase 2.",
            "location": "T-Hub Phase 2, Hyderabad"
        }
        self.assertTrue(formatter.is_hyderabad_offline(item))
        self.assertTrue(formatter.is_location_allowed(item))
        badge = formatter.get_location_badge(item)
        self.assertEqual(badge, "[HYDERABAD - OFFLINE]")
        loc_str = formatter.format_location_string(item)
        self.assertEqual(loc_str, "[HYDERABAD - OFFLINE] Hyderabad (T-Hub Phase 2)")

    def test_no_duplicate_badge_in_draft_title(self):
        # When item title already includes badge
        item = {
            "category": "hackathon",
            "title": "AI Innovation Hackathon [HYDERABAD - OFFLINE]",
            "url": "https://example.com/hack",
            "summary": "In-person hackathon at IIIT Hyderabad.",
            "location": "IIIT Hyderabad, Gachibowli",
            "deadline": "2026-03-30"
        }
        draft = formatter.format_draft(item)
        # Should not have duplicate badge: '[HYDERABAD - OFFLINE] [HYDERABAD - OFFLINE]'
        self.assertEqual(draft.count("[HYDERABAD - OFFLINE] [HYDERABAD - OFFLINE]"), 0)
        self.assertIn("📍 Where: [HYDERABAD - OFFLINE] Hyderabad (IIIT Hyderabad)", draft)

    def test_hyderabad_formatters_resilient_to_none_and_types(self):
        self.assertEqual(formatter.format_location_string(None), "[ONLINE] Online")
        self.assertEqual(formatter.format_location_string({}), "[ONLINE] Online")
        self.assertEqual(formatter.get_location_badge(None), "[ONLINE]")
        self.assertEqual(formatter.get_location_badge({}), "[ONLINE]")
        self.assertEqual(formatter.get_location_tag(None), "📍 Location: [ONLINE] Online")
        self.assertEqual(formatter.get_location_tag({}), "📍 Location: [ONLINE] Online")

        # Non-string fields in item
        weird_item = {
            "category": 123,
            "title": 456,
            "location": None,
            "summary": ["nested"],
            "url": None
        }
        self.assertFalse(formatter.is_hyderabad_offline(weird_item))
        self.assertTrue(formatter.is_location_allowed(weird_item))
        draft = formatter.format_draft(weird_item)
        self.assertIn("[ONLINE]", draft)

    @patch("collectors.fetch_data")
    def test_hyderabad_collectors_error_handling(self, mock_fetch):
        # When network returns None (simulating HTTP error / timeout)
        mock_fetch.return_value = None
        self.assertEqual(collectors.fetch_commudle_events(), [])
        self.assertEqual(collectors.fetch_luma_hyderabad_events(), [])
        self.assertEqual(collectors.fetch_meetup_hyderabad_events(), [])
        self.assertEqual(collectors.fetch_unstop_hackathons(), [])
        self.assertEqual(collectors.fetch_devpost_hyderabad_hackathons(), [])
        self.assertEqual(collectors.fetch_hyderabad_venue_events(), [])

        # When network returns corrupted/invalid JSON bytes
        mock_fetch.return_value = b"{ invalid json raw bytes : [ }}"
        self.assertEqual(collectors.fetch_commudle_events(), [])
        self.assertEqual(collectors.fetch_luma_hyderabad_events(), [])
        self.assertEqual(collectors.fetch_unstop_hackathons(), [])


class TestQualityGateRegression(unittest.TestCase):
    """Regression tests verifying quality_gate decisions on specified fixtures (Task 8)."""

    def test_rust_article_rejected(self):
        item = {
            "title": "Rust Events Nov-Dec 2026: Structured Format Needed for Easier Analysis",
            "summary": "Introduction " * 300,
            "link": "https://dev.to/x",
            "source": "Dev Events & Workshops"
        }
        decision, reason, _ = quality_gate.gate(item)
        self.assertEqual(decision, "reject")
        self.assertIn("article", reason)

    def test_dubai_7s_rejected(self):
        item = {
            "title": "Emirates Dubai 7s 2026: Dates, Tickets, Lineup and the Weekend",
            "summary": "Rugby sevens tournament with concerts and tickets.",
            "link": "https://dev.to/y"
        }
        decision, reason, _ = quality_gate.gate(item)
        self.assertEqual(decision, "reject")
        self.assertIn("off-topic", reason)

    def test_dubai_shopping_festival_rejected(self):
        item = {
            "title": "Dubai Shopping Festival 2026-27: 45 Days of Sales, Raffles",
            "summary": "Discounts and raffles.",
            "link": "https://dev.to/z"
        }
        decision, reason, _ = quality_gate.gate(item)
        self.assertEqual(decision, "reject")
        self.assertIn("off-topic", reason)

    def test_offline_bangalore_meetup_rejected(self):
        item = {
            "title": "AI Meetup in Bangalore",
            "summary": "In-person at Indiranagar, Bangalore.",
            "link": "https://meetup.com/x"
        }
        decision, reason, _ = quality_gate.gate(item)
        self.assertEqual(decision, "reject")
        self.assertIn("offline in other city", reason)

    def test_hackglobal_accepted(self):
        item = {
            "title": "HackGlobal by Polaris School of Technology",
            "summary": "A global online hackathon with builders from five countries.",
            "link": "https://hackculture.io"
        }
        decision, reason, clean = quality_gate.gate(item)
        self.assertEqual(decision, "accept")
        self.assertEqual(reason, "ok")
        self.assertEqual(clean.get("badge"), "ONLINE")
        self.assertLessEqual(len(clean.get("summary", "")), 220)

    def test_ksquare_walkin_accepted(self):
        item = {
            "title": "Walk-in drive at Ksquare Group: Salesforce and Data Engineering",
            "summary": "Five roles. Venue: Nanakramguda, Hyderabad.",
            "link": "https://linkedin.com/x"
        }
        decision, reason, clean = quality_gate.gate(item)
        self.assertEqual(decision, "accept")
        self.assertEqual(reason, "ok")
        self.assertEqual(clean.get("badge"), "HYDERABAD - OFFLINE")
        self.assertLessEqual(len(clean.get("summary", "")), 220)

    def test_formatter_respects_gate_badges(self):
        # When gate sets badge to ONLINE, HYDERABAD - OFFLINE, or None
        self.assertEqual(formatter.get_location_badge({"badge": "ONLINE"}), "[ONLINE]")
        self.assertEqual(formatter.get_location_badge({"badge": "HYDERABAD - OFFLINE"}), "[HYDERABAD - OFFLINE]")
        self.assertEqual(formatter.get_location_badge({"badge": None}), "")
        self.assertEqual(formatter.get_location_badge({"badge": ""}), "")

        # When badge is None, draft must NOT default to [ONLINE]
        item = {
            "title": "Special Tech Workshop",
            "summary": "Hands-on session with cloud tools.",
            "badge": None,
            "category": "event",
            "url": "https://example.com/special"
        }
        draft = formatter.format_draft(item)
        self.assertNotIn("[ONLINE]", draft)

    def test_devpost_subdomain_trusted_in_gate(self):
        item = {
            "title": "PayPal AI Hackathon",
            "summary": "Online developer hackathon on Devpost. Themes: Machine Learning/AI.",
            "location": "Online",
            "link": "https://paypalaihackathon.devpost.com"
        }
        decision, reason, clean = quality_gate.gate(item)
        self.assertEqual(decision, "accept")
        self.assertEqual(reason, "ok")
        self.assertEqual(clean.get("badge"), "ONLINE")


class TestFeedRefreshAndAutoUpdate(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_feed.db")
        self.md_path = os.path.join(self.temp_dir.name, "PENDING_DRAFTS_TEST.md")
        self.html_path = os.path.join(self.temp_dir.name, "legion_feed.html")
        self.posts_path = os.path.join(self.temp_dir.name, "posts.json")
        db.init_db(self.db_path)

        # Create a dummy base html template in temp dir
        with open(self.html_path, "w", encoding="utf-8") as f:
            f.write("""<!DOCTYPE html><html><body>
<div id="tabs"></div><div id="feed"></div><div id="count"></div>
<script>
var TYPES={all:"All"};var TYPE_LABELS={news:"News"};
var SEED = [];
var TODAY = "2026-11-08";
function tabs(){} function render(){}
</script></body></html>""")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_export_posts_json_schema_and_dedup(self):
        # Insert test items in DB
        db.insert_item({
            "source": "Devpost",
            "category": "hackathon",
            "title": "Global AI Challenge",
            "url": "https://devpost.com/hackathons/global-ai-challenge",
            "summary": "Online AI Hackathon with $25k prize",
            "deadline": "2026-12-01",
            "location": "Online",
            "badge": "ONLINE",
            "status": "pending"
        }, self.db_path)

        db.insert_item({
            "source": "Meetup",
            "category": "event",
            "title": "T-Hub Hyderabad AI Meetup",
            "url": "https://meetup.com/events/thub-ai",
            "summary": "In-person meetup at T-Hub",
            "deadline": "2026-11-20",
            "location": "Hyderabad",
            "badge": "HYDERABAD - OFFLINE",
            "status": "pending"
        }, self.db_path)

        # Rejected item should be excluded from posts.json
        rej_id = db.insert_item({
            "source": "Junk",
            "category": "news",
            "title": "Spam Casino Post",
            "url": "https://example.com/spam",
            "summary": "Spam",
            "status": "pending"
        }, self.db_path)
        db.update_item_status(rej_id, "rejected", self.db_path)

        posts = pipeline.export_posts_json(db_path=self.db_path, output_path=self.posts_path)
        self.assertTrue(os.path.exists(self.posts_path))
        self.assertGreaterEqual(len(posts), 2)

        # Verify schema
        found_ai = False
        found_thub = False
        for p in posts:
            self.assertIn("id", p)
            self.assertIn("type", p)
            self.assertIn("title", p)
            self.assertIn("sum", p)
            self.assertIn("date", p)
            self.assertIn("where", p)
            self.assertIn("badge", p)
            self.assertIn("src", p)
            self.assertIn("link", p)
            self.assertIn("posted", p)
            self.assertIn("status", p)
            self.assertNotEqual(p.get("status"), "rejected")

            if "Global AI Challenge" in p["title"]:
                found_ai = True
                self.assertEqual(p["badge"], "ONLINE")
                self.assertEqual(p["date"], "2026-12-01")
            if "T-Hub" in p["title"]:
                found_thub = True
                self.assertEqual(p["badge"], "HYDERABAD - OFFLINE")

        self.assertTrue(found_ai)
        self.assertTrue(found_thub)

    @patch("collectors2.collect")
    def test_refresh_feed_function(self, mock_collect):
        mock_collect.return_value = [
            {
                "source": "Devpost",
                "category": "hackathon",
                "title": "PayPal AI Hackathon",
                "url": "https://paypalaihackathon.devpost.com",
                "link": "https://paypalaihackathon.devpost.com",
                "summary": "Online developer hackathon on Devpost. Machine Learning/AI.",
                "deadline": "2026-11-30",
                "location": "Online"
            }
        ]

        res = pipeline.refresh_feed(
            db_path=self.db_path,
            posts_path=self.posts_path,
            markdown_path=self.md_path,
            html_path=self.html_path,
            limit_per_feed=2
        )

        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["new_pending"], 1)
        self.assertGreaterEqual(res["total_items"], 1)
        self.assertIn("timestamp", res)
        self.assertEqual(res["message"], "Feed refreshed successfully")

        # Check that posts.json, PENDING_DRAFTS_TEST.md, and legion_feed.html are created/updated
        self.assertTrue(os.path.exists(self.posts_path))
        self.assertTrue(os.path.exists(self.md_path))
        self.assertTrue(os.path.exists(self.html_path))

    @patch("collectors2.collect")
    def test_http_refresh_endpoint_get_and_post(self, mock_collect):
        mock_collect.return_value = [
            {
                "source": "Devpost",
                "category": "hackathon",
                "title": "Meta VR Start Developer Competition",
                "url": "https://meta-vr-2026.devpost.com",
                "link": "https://meta-vr-2026.devpost.com",
                "summary": "Online developer hackathon on Devpost. AR/VR Machine Learning.",
                "deadline": "2026-12-15",
                "location": "Online"
            }
        ]

        outer_dir = self.temp_dir.name

        # Start a test HTTP server on ephemeral port (port 0)
        class TestHandler(pipeline.FeedHTTPRequestHandler):
            server_filename = "legion_feed.html"
            db_path = self.db_path
            posts_path = self.posts_path
            markdown_path = self.md_path
            html_path = self.html_path

            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=outer_dir, **kwargs)

        server = socketserver.TCPServer(("127.0.0.1", 0), TestHandler)
        port = server.server_address[1]
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()

        base_url = f"http://127.0.0.1:{port}"

        try:
            # 1. Test POST /api/refresh
            req = urllib.request.Request(f"{base_url}/api/refresh", method="POST")
            with urllib.request.urlopen(req, timeout=5) as resp:
                self.assertEqual(resp.status, 200)
                self.assertEqual(resp.headers.get("Content-Type"), "application/json; charset=utf-8")
                self.assertEqual(resp.headers.get("Access-Control-Allow-Origin"), "*")
                data = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(data["status"], "ok")
                self.assertEqual(data["new_pending"], 1)

            # 2. Test GET /api/refresh
            req_get = urllib.request.Request(f"{base_url}/api/refresh", method="GET")
            with urllib.request.urlopen(req_get, timeout=5) as resp:
                self.assertEqual(resp.status, 200)
                self.assertEqual(resp.headers.get("Access-Control-Allow-Origin"), "*")
                data = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(data["status"], "ok")
                # Second run, same item -> 0 new pending
                self.assertEqual(data["new_pending"], 0)

            # 3. Test OPTIONS /api/refresh (CORS preflight)
            req_opt = urllib.request.Request(f"{base_url}/api/refresh", method="OPTIONS")
            with urllib.request.urlopen(req_opt, timeout=5) as resp:
                self.assertEqual(resp.status, 204)
                self.assertEqual(resp.headers.get("Access-Control-Allow-Origin"), "*")
                self.assertIn("POST", resp.headers.get("Access-Control-Allow-Methods", ""))

            # 4. Test GET /posts.json
            with urllib.request.urlopen(f"{base_url}/posts.json", timeout=5) as resp:
                self.assertEqual(resp.status, 200)
                posts_data = json.loads(resp.read().decode("utf-8"))
                self.assertIsInstance(posts_data, list)
                self.assertGreaterEqual(len(posts_data), 1)

            # 5. Test 404 for unknown POST
            req_404 = urllib.request.Request(f"{base_url}/api/nonexistent", method="POST")
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(req_404, timeout=5)
            self.assertEqual(ctx.exception.code, 404)

        finally:
            server.shutdown()
            server.server_close()

    @patch("pipeline.refresh_feed")
    def test_start_auto_updater(self, mock_refresh):
        stop_event = threading.Event()
        # Pass a very short interval: 0.001 minutes = 0.06 seconds
        thread, event = pipeline.start_auto_updater(
            interval_minutes=0.001,
            stop_event=stop_event,
            db_path=self.db_path,
            posts_path=self.posts_path,
            markdown_path=self.md_path,
            html_path=self.html_path
        )
        self.assertTrue(thread.is_alive())
        self.assertTrue(thread.daemon)

        # Wait a moment for worker to run at least once
        time.sleep(0.15)
        self.assertGreaterEqual(mock_refresh.call_count, 1)

        # Signal stop and join
        event.set()
        thread.join(timeout=1.0)
        self.assertFalse(thread.is_alive())

    def test_legion_feed_html_ui_elements(self):
        feed_html_path = config.LEGION_FEED_HTML_PATH
        self.assertTrue(os.path.exists(feed_html_path))
        with open(feed_html_path, "r", encoding="utf-8") as f:
            html = f.read()

        # Update button exists
        self.assertIn('id="refresh-btn"', html)
        self.assertIn('Update Feed', html)
        # Offline mode handling
        self.assertIn('window.location.protocol === "file:"', html)
        self.assertIn('Offline file mode', html)
        # Fetch /api/refresh endpoint
        self.assertIn('/api/refresh', html)
        # Cache-busting posts.json fetch
        self.assertIn('posts.json?t=', html)
        # Auto-poll interval
        self.assertIn('setInterval', html)

    def test_export_posts_json_empty_db(self):
        empty_posts = os.path.join(self.temp_dir.name, "empty_posts.json")
        empty_db = os.path.join(self.temp_dir.name, "empty.db")
        db.init_db(empty_db)
        posts = pipeline.export_posts_json(db_path=empty_db, output_path=empty_posts)
        self.assertTrue(os.path.exists(empty_posts))
        self.assertIsInstance(posts, list)

    @patch("collectors2.collect")
    def test_quality_gate_filters_during_refresh(self, mock_collect):
        # 1 valid, 1 junk (casino), 1 other city offline (Bangalore)
        mock_collect.return_value = [
            {
                "source": "Devpost",
                "category": "hackathon",
                "title": "Legion Python Agent Hackathon",
                "url": "https://devpost.com/hackathons/legion-agent-hack",
                "link": "https://devpost.com/hackathons/legion-agent-hack",
                "summary": "Build agentic Python applications.",
                "deadline": "2026-12-10",
                "location": "Online"
            },
            {
                "source": "Web",
                "category": "news",
                "title": "Grand Casino Free Spins Bonus",
                "url": "https://casino-online.example/bonus",
                "link": "https://casino-online.example/bonus",
                "summary": "Betting and gambling casino vouchers.",
                "location": "Online"
            },
            {
                "source": "Meetup",
                "category": "event",
                "title": "Bangalore Python In-Person Meetup",
                "url": "https://meetup.com/events/bangalore-py",
                "link": "https://meetup.com/events/bangalore-py",
                "summary": "In-person at Indiranagar, Bangalore.",
                "location": "Bangalore"
            }
        ]

        res = pipeline.refresh_feed(
            db_path=self.db_path,
            posts_path=self.posts_path,
            markdown_path=self.md_path,
            html_path=self.html_path,
            limit_per_feed=5
        )

        self.assertEqual(res["status"], "ok")
        # Only the valid tech online hackathon should be accepted
        self.assertEqual(res["new_pending"], 1)

        with open(self.posts_path, "r", encoding="utf-8") as f:
            posts = json.load(f)

        urls = [p.get("link") for p in posts]
        self.assertIn("https://devpost.com/hackathons/legion-agent-hack", urls)
        self.assertNotIn("https://casino-online.example/bonus", urls)
        self.assertNotIn("https://meetup.com/events/bangalore-py", urls)

    @patch("collectors2.collect")
    def test_concurrent_refresh_lock(self, mock_collect):
        # Verify multiple threads calling refresh_feed concurrently run safely
        mock_collect.return_value = [
            {
                "source": "Devpost",
                "category": "hackathon",
                "title": "Concurrent Hackathon",
                "url": "https://concurrent.devpost.com",
                "link": "https://concurrent.devpost.com",
                "summary": "Machine learning hackathon.",
                "location": "Online"
            }
        ]

        errors = []
        def call_refresh():
            try:
                res = pipeline.refresh_feed(
                    db_path=self.db_path,
                    posts_path=self.posts_path,
                    markdown_path=self.md_path,
                    html_path=self.html_path
                )
                if res["status"] != "ok":
                    errors.append(res)
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=call_refresh) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        self.assertTrue(os.path.exists(self.posts_path))

    @patch("pipeline.refresh_feed")
    def test_http_refresh_server_error_500(self, mock_refresh):
        mock_refresh.side_effect = RuntimeError("Simulated collector crash")
        outer_dir = self.temp_dir.name

        class ErrorHandler(pipeline.FeedHTTPRequestHandler):
            server_filename = "legion_feed.html"
            db_path = self.db_path
            posts_path = self.posts_path
            markdown_path = self.md_path
            html_path = self.html_path

            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=outer_dir, **kwargs)

        server = socketserver.TCPServer(("127.0.0.1", 0), ErrorHandler)
        port = server.server_address[1]
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()

        base_url = f"http://127.0.0.1:{port}"
        try:
            req = urllib.request.Request(f"{base_url}/api/refresh", method="POST")
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(req, timeout=5)
            self.assertEqual(ctx.exception.code, 500)
            err_body = json.loads(ctx.exception.read().decode("utf-8"))
            self.assertEqual(err_body["status"], "error")
            self.assertIn("Simulated collector crash", err_body["message"])
        finally:
            server.shutdown()
            server.server_close()

    def test_export_posts_json_purges_rejected_items_even_if_in_previous_posts_json(self):
        # 1. Insert an item as pending
        row_id = db.insert_item({
            "source": "Devpost",
            "category": "hackathon",
            "title": "Crypto Hackathon",
            "url": "https://devpost.com/hackathons/crypto-hack",
            "summary": "Cryptocurrency coding challenge",
            "status": "pending"
        }, self.db_path)

        # 2. Export to posts.json (item is present)
        posts_1 = pipeline.export_posts_json(db_path=self.db_path, output_path=self.posts_path)
        self.assertTrue(any(p["link"] == "https://devpost.com/hackathons/crypto-hack" for p in posts_1))

        # 3. Mark the item as rejected in SQLite DB
        db.update_item_status(row_id, "rejected", self.db_path)

        # 4. Export again to the existing posts.json file
        posts_2 = pipeline.export_posts_json(db_path=self.db_path, output_path=self.posts_path)

        # 5. Verify the rejected item was strictly purged from posts.json
        self.assertFalse(any(p["link"] == "https://devpost.com/hackathons/crypto-hack" for p in posts_2))
        self.assertFalse(any(p.get("status") == "rejected" for p in posts_2))

    def test_http_posts_json_no_cache_headers(self):
        # Insert test item and export posts.json
        db.insert_item({
            "source": "Test",
            "category": "news",
            "title": "Cache Control Test",
            "url": "https://test.example/cache-test",
            "summary": "Verifying Cache-Control headers",
            "status": "pending"
        }, self.db_path)
        pipeline.export_posts_json(db_path=self.db_path, output_path=self.posts_path)

        outer_dir = self.temp_dir.name
        class CacheHandler(pipeline.FeedHTTPRequestHandler):
            server_filename = "legion_feed.html"
            db_path = self.db_path
            posts_path = self.posts_path
            markdown_path = self.md_path
            html_path = self.html_path

            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=outer_dir, **kwargs)

        server = socketserver.TCPServer(("127.0.0.1", 0), CacheHandler)
        port = server.server_address[1]
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()

        base_url = f"http://127.0.0.1:{port}"
        try:
            req = urllib.request.Request(f"{base_url}/posts.json")
            with urllib.request.urlopen(req, timeout=5) as resp:
                self.assertEqual(resp.status, 200)
                cache_header = resp.headers.get("Cache-Control", "")
                self.assertIn("no-cache", cache_header)
                self.assertIn("no-store", cache_header)
                self.assertEqual(resp.headers.get("Access-Control-Allow-Origin"), "*")
                data = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(any(p["link"] == "https://test.example/cache-test" for p in data))
        finally:
            server.shutdown()
            server.server_close()

    @patch("collectors2.collect")
    def test_http_refresh_with_post_body_payload(self, mock_collect):
        mock_collect.return_value = [
            {
                "source": "Devpost",
                "category": "hackathon",
                "title": "Body Payload Hack",
                "url": "https://devpost.com/hackathons/body-payload-hack",
                "link": "https://devpost.com/hackathons/body-payload-hack",
                "summary": "Hackathon tested with POST body",
                "location": "Online"
            }
        ]
        outer_dir = self.temp_dir.name

        class BodyHandler(pipeline.FeedHTTPRequestHandler):
            server_filename = "legion_feed.html"
            db_path = self.db_path
            posts_path = self.posts_path
            markdown_path = self.md_path
            html_path = self.html_path

            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=outer_dir, **kwargs)

        server = socketserver.TCPServer(("127.0.0.1", 0), BodyHandler)
        port = server.server_address[1]
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()

        base_url = f"http://127.0.0.1:{port}"
        try:
            payload = json.dumps({"force": True, "source": "all"}).encode("utf-8")
            req = urllib.request.Request(
                f"{base_url}/api/refresh",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(data["status"], "ok")
                self.assertGreaterEqual(data["total_items"], 1)
        finally:
            server.shutdown()
            server.server_close()

    @patch("collectors2.fetch_raw")
    def test_collectors2_collect_resilient_to_single_source_crash(self, mock_fetch):
        # Source 1 raises an unexpected exception
        # Source 2 returns valid XML feed
        xml_content = b"""<?xml version="1.0" encoding="utf-8"?>
        <rss version="2.0">
            <channel>
                <title>Test Feed</title>
                <item>
                    <title>Resilient AI Workshop</title>
                    <link>https://example.com/resilient-ai</link>
                    <description>Safe workshop summary</description>
                </item>
            </channel>
        </rss>"""

        def side_effect_fetch(url, headers=None, timeout=10):
            if "hacker-news" in url:
                raise RuntimeError("Unexpected socket catastrophe")
            return xml_content, None

        mock_fetch.side_effect = side_effect_fetch
        items = collectors2.collect(limit=2)
        # Verify collection completed successfully despite the crashed source
        self.assertIsInstance(items, list)
        self.assertTrue(any(it.get("title") == "Resilient AI Workshop" for it in items))

    def test_export_posts_json_truncates_long_summaries(self):
        """Verifies that export_posts_json caps summaries to <= 450 chars with sentence boundary."""
        long_text = "This is a very long opportunity description for an engineering role. " * 20
        db.insert_item({
            "source": "Test Feed",
            "category": "job",
            "title": "Junior AI Engineer",
            "url": "https://test.example/long-summary-job",
            "summary": long_text,
            "status": "pending"
        }, self.db_path)
        
        posts = pipeline.export_posts_json(db_path=self.db_path, output_path=self.posts_path)
        item = [p for p in posts if p["link"] == "https://test.example/long-summary-job"][0]
        self.assertLessEqual(len(item["sum"]), 450)
        self.assertTrue(item["sum"].endswith("...") or item["sum"].endswith("."))

    def test_export_posts_json_strips_spam_tokens(self):
        """Verifies that verification tokens like **BENEVOLENT** are stripped from export."""
        raw_text = "Exciting AI annotation work. Please mention the word **BENEVOLENT** and tag #RMTM2LjIzMi4yMjcuMjAy when applying."
        db.insert_item({
            "source": "RemoteOK",
            "category": "job",
            "title": "AI Content Specialist",
            "url": "https://test.example/spam-token-job",
            "summary": raw_text,
            "status": "pending"
        }, self.db_path)
        
        posts = pipeline.export_posts_json(db_path=self.db_path, output_path=self.posts_path)
        item = [p for p in posts if p["link"] == "https://test.example/spam-token-job"][0]
        self.assertNotIn("**BENEVOLENT**", item["sum"])
        self.assertNotIn("#RMTM2LjIzMi4yMjcuMjAy", item["sum"])


class TestLegionEnhancements(unittest.TestCase):
    """
    Tests covering the new Legion requirements:
    1. Item with deadline yesterday is absent from /posts.json.
    2. Item with no date, first_seen 8 days ago is expired; at 6 days it is shown.
    3. Item with deadline in 20 days and first_seen 10 days ago is still shown.
    4. Re-collecting an expired URL does not create a new active row.
    5. Missing event date on a hackathon goes to review, never accept.
    6. Same event from two URLs collapses to one row.
    7. "Bangalore" and "Bengaluru" both map to bengaluru.
    8. Enricher survives timeout, 404 and malformed HTML without raising.
    9. Formatter omits missing fields and stays under 400 chars.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_enhancements.db")
        self.posts_path = os.path.join(self.temp_dir.name, "posts.json")
        db.init_db(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_item_with_deadline_yesterday_is_absent_from_posts_json(self):
        """Item with deadline yesterday is absent from /posts.json."""
        yesterday_str = (config.now_ist() - timedelta(days=1)).strftime("%Y-%m-%d")
        future_str = (config.now_ist() + timedelta(days=5)).strftime("%Y-%m-%d")

        # Expired item (deadline yesterday)
        db.insert_item({
            "source": "Devpost",
            "category": "hackathon",
            "title": "Yesterday Hackathon",
            "url": "https://devpost.com/yesterday-hack",
            "deadline": yesterday_str,
            "status": "accepted"
        }, self.db_path)

        # Active future item
        db.insert_item({
            "source": "Devpost",
            "category": "hackathon",
            "title": "Future Hackathon",
            "url": "https://devpost.com/future-hack",
            "deadline": future_str,
            "status": "accepted"
        }, self.db_path)

        posts = pipeline.export_posts_json(db_path=self.db_path, output_path=self.posts_path)
        post_urls = [p["link"] for p in posts]

        self.assertNotIn("https://devpost.com/yesterday-hack", post_urls)
        self.assertIn("https://devpost.com/future-hack", post_urls)

    def test_item_no_date_ttl_expiry(self):
        """Item with no date, first_seen 8 days ago is expired; at 6 days it is shown."""
        eight_days_ago = (config.now_ist() - timedelta(days=8)).strftime("%Y-%m-%d %H:%M:%S")
        six_days_ago = (config.now_ist() - timedelta(days=6)).strftime("%Y-%m-%d %H:%M:%S")

        # Item 1: first_seen 8 days ago, no date (TTL 7 days -> expired)
        db.insert_item({
            "source": "Lobste.rs",
            "category": "news",
            "title": "Old News Item 8 Days",
            "url": "https://lobste.rs/s/old-8-days",
            "first_seen": eight_days_ago,
            "status": "accepted"
        }, self.db_path)

        # Item 2: first_seen 6 days ago, no date (TTL 7 days -> active)
        db.insert_item({
            "source": "Lobste.rs",
            "category": "news",
            "title": "Recent News Item 6 Days",
            "url": "https://lobste.rs/s/recent-6-days",
            "first_seen": six_days_ago,
            "status": "accepted"
        }, self.db_path)

        # Run expiry job
        db.expire_old_items(self.db_path)

        posts = pipeline.export_posts_json(db_path=self.db_path, output_path=self.posts_path)
        post_urls = [p["link"] for p in posts]

        # 8-day-old item must be absent (expired)
        self.assertNotIn("https://lobste.rs/s/old-8-days", post_urls)
        # 6-day-old item must be shown
        self.assertIn("https://lobste.rs/s/recent-6-days", post_urls)

        # Check DB status
        with db.get_db_connection(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status FROM items WHERE url='https://lobste.rs/s/old-8-days'")
            row = cursor.fetchone()
            self.assertEqual(row["status"], "expired")

    def test_item_with_deadline_in_20_days_first_seen_10_days_ago(self):
        """Item with deadline in 20 days and first_seen 10 days ago is still shown."""
        ten_days_ago = (config.now_ist() - timedelta(days=10)).strftime("%Y-%m-%d %H:%M:%S")
        twenty_days_ahead = (config.now_ist() + timedelta(days=20)).strftime("%Y-%m-%d")

        db.insert_item({
            "source": "Devpost",
            "category": "hackathon",
            "title": "Long Running Hackathon",
            "url": "https://devpost.com/long-running",
            "first_seen": ten_days_ago,
            "deadline": twenty_days_ahead,
            "status": "accepted"
        }, self.db_path)

        posts = pipeline.export_posts_json(db_path=self.db_path, output_path=self.posts_path)
        post_urls = [p["link"] for p in posts]

        self.assertIn("https://devpost.com/long-running", post_urls)

    def test_recollecting_expired_url_does_not_create_new_active_row(self):
        """Re-collecting an expired URL does not create a new active row."""
        old_seen = (config.now_ist() - timedelta(days=15)).strftime("%Y-%m-%d %H:%M:%S")
        expired_at = (config.now_ist() - timedelta(days=8)).strftime("%Y-%m-%d %H:%M:%S")

        # Initial insertion of an expired item
        inserted_id = db.insert_item({
            "source": "Unstop",
            "category": "hackathon",
            "title": "Expired Contest",
            "url": "https://unstop.com/competitions/expired-contest-101",
            "first_seen": old_seen,
            "expires_at": expired_at,
            "status": "expired"
        }, self.db_path)
        self.assertIsNotNone(inserted_id)

        # Attempt to re-collect the same URL with active/accepted status
        res = db.insert_item({
            "source": "Unstop",
            "category": "hackathon",
            "title": "Expired Contest (Re-scraped)",
            "url": "https://unstop.com/competitions/expired-contest-101",
            "status": "accepted"
        }, self.db_path)

        # Must not insert a new row
        self.assertIsNone(res)

        # Database must still have only 1 row, and status must remain 'expired'
        with db.get_db_connection(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, status, first_seen FROM items WHERE url='https://unstop.com/competitions/expired-contest-101'")
            rows = cursor.fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["status"], "expired")
            # first_seen must never be overwritten on re-collection
            self.assertEqual(rows[0]["first_seen"], old_seen)

    def test_missing_event_date_on_hackathon_goes_to_review_never_accept(self):
        """Missing event date on a hackathon goes to review, never accept."""
        raw_hackathon = {
            "source": "Devpost",
            "category": "hackathon",
            "title": "Undated AI Hackathon",
            "url": "https://devpost.com/hackathons/undated-ai",
            "link": "https://devpost.com/hackathons/undated-ai",
            "summary": "AI hackathon with exciting challenges for builders.",
            "location": "Online",
            "status": "accepted"  # even if upstream marked it accepted
        }

        with patch("enricher.fetch_page", return_value="<html><body><h1>Undated AI Hackathon</h1><p>Join our online hackathon!</p></body></html>"):
            enriched = enricher.enrich_item(raw_hackathon)

        self.assertEqual(enriched.get("status"), "review")
        self.assertEqual(enriched.get("reason"), "no date")
        self.assertNotEqual(enriched.get("status"), "accept")
        self.assertNotEqual(enriched.get("status"), "accepted")

    def test_same_event_from_two_urls_collapses_to_one_row(self):
        """Same event from two URLs collapses to one row via fingerprint."""
        event_unstop = {
            "source": "Unstop",
            "category": "hackathon",
            "title": "India AI Hackathon 2026",
            "url": "https://unstop.com/competitions/india-ai-hack-2026",
            "deadline": "2026-12-25",
            "city": "hyderabad",
            "status": "accepted"
        }
        event_devpost = {
            "source": "Devpost",
            "category": "hackathon",
            "title": "India AI Hackathon 2026",
            "url": "https://devpost.com/hackathons/india-ai-hack-2026",
            "deadline": "2026-12-25",
            "city": "hyderabad",
            "status": "accepted"
        }

        # First URL insert succeeds
        id1 = db.insert_item(event_unstop, self.db_path)
        self.assertIsNotNone(id1)

        # Second URL with identical title, date, city must collapse and return None
        id2 = db.insert_item(event_devpost, self.db_path)
        self.assertIsNone(id2)

        # Total items in DB must be exactly 1
        with db.get_db_connection(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT count(*) as cnt FROM items")
            self.assertEqual(cursor.fetchone()["cnt"], 1)

    def test_bangalore_and_bengaluru_both_map_to_bengaluru(self):
        """'Bangalore' and 'Bengaluru' both map to bengaluru."""
        # Test config normalization
        self.assertEqual(config.normalize_city("Bangalore"), "bengaluru")
        self.assertEqual(config.normalize_city("Bengaluru"), "bengaluru")
        self.assertEqual(config.normalize_city("Koramangala, Bangalore"), "bengaluru")
        self.assertEqual(config.normalize_city("Indiranagar, Bengaluru"), "bengaluru")
        self.assertEqual(config.normalize_city("BLR Tech Hub"), "bengaluru")

        # Test enricher detection
        self.assertEqual(enricher.detect_city("Whitefield, Bangalore"), "bengaluru")
        self.assertEqual(enricher.detect_city("Electronic City, Bengaluru"), "bengaluru")

    @patch("enricher.fetch_page")
    def test_enricher_survives_timeout_404_and_malformed_html(self, mock_fetch):
        """Enricher survives timeout, 404 and malformed HTML without raising."""
        item_base = {
            "source": "Devpost",
            "category": "job",
            "title": "Platform Engineer",
            "url": "https://example.com/job/101",
            "location": "Bengaluru",
            "summary": "Building scalable developer tools"
        }

        # 1. Timeout / URLError
        mock_fetch.side_effect = urllib.error.URLError("Connection timed out")
        res1 = enricher.enrich_item(item_base)
        self.assertIsInstance(res1, dict)
        self.assertEqual(res1.get("title"), "Platform Engineer")

        # 2. HTTP 404 error
        mock_fetch.side_effect = urllib.error.HTTPError(
            url="https://example.com/job/101",
            code=404,
            msg="Not Found",
            hdrs={},
            fp=None
        )
        res2 = enricher.enrich_item(item_base)
        self.assertIsInstance(res2, dict)
        self.assertEqual(res2.get("title"), "Platform Engineer")

        # 3. Malformed HTML
        mock_fetch.side_effect = None
        mock_fetch.return_value = "<<<malformed <<<<unclosed <html><head><script>alert(1)</head><div>broken body"
        res3 = enricher.enrich_item(item_base)
        self.assertIsInstance(res3, dict)
        self.assertEqual(res3.get("title"), "Platform Engineer")

    def test_formatter_omits_missing_fields_and_stays_under_400_chars(self):
        """Formatter omits missing fields and generates valid WhatsApp templates."""
        # Minimal item with only title and URL (missing fee, prize, eligibility, venue, date)
        minimal_item = {
            "category": "event",
            "title": "Minimal Python Meetup",
            "url": "https://example.com/min-py",
            "badge": "ONLINE"
        }
        res_min = formatter.format_whatsapp(minimal_item)

        self.assertNotIn("Fee:", res_min)
        self.assertNotIn("Prize:", res_min)
        self.assertNotIn("Eligibility:", res_min)
        self.assertNotIn("Venue:", res_min)
        self.assertNotIn("Date/Deadline:", res_min)
        self.assertIn("https://example.com/min-py", res_min)

        # Maximal item with full details
        maximal_item = {
            "category": "hackathon",
            "title": "Ultra Scalable Global Autonomous Multi-Agent AI Hackathon",
            "url": "https://devpost.com/hackathons/ultra-scalable-hackathon",
            "summary": "Join world-class AI developers building autonomous agents on cutting edge models with top tier mentors and sponsors.",
            "deadline": "2026-12-30",
            "venue": "T-Hub Phase 2, Hyderabad",
            "fee": "Free",
            "prize": "₹50,00,000",
            "skill_tags": "Python, Docker, FastAPI",
            "eligibility": "Students & Developers",
            "city": "hyderabad",
            "mode": "OFFLINE",
            "badge": "HYDERABAD - OFFLINE"
        }
        res_max = formatter.format_whatsapp(maximal_item)
        self.assertTrue(250 <= len(res_max) <= 750, f"Length {len(res_max)} not in [250, 750]")
        self.assertIn("🏆 *Prizes & Perks:* ₹50,00,000", res_max)
        self.assertIn("📝 *Overview:*", res_max)

        # Job item test
        job_item = {
            "category": "job",
            "title": "Software Engineer",
            "role": "Software Engineer",
            "company": "Anthropic Partner Hub",
            "url": "https://example.com/careers/ai-infra",
            "summary": "Design and optimize distributed model training cluster infrastructure.",
            "compensation": "₹35-45 LPA",
            "skill_tags": "Python, PyTorch, Kubernetes",
            "experience_level": "FRESHER_INTERN",
            "location": "Bengaluru",
            "venue": "Bengaluru",
            "city": "bengaluru",
            "badge": "BENGALURU - OFFLINE"
        }
        res_job = formatter.format_whatsapp(job_item)
        self.assertTrue(250 <= len(res_job) <= 750, f"Length {len(res_job)} not in [250, 750]")
        self.assertIn("💼 *Software Engineer*", res_job)
        self.assertIn("💰 *CTC / Stipend:* ₹35-45 LPA", res_job)
        self.assertIn("🛠️ *Tech Stack:* Python, PyTorch, Kubernetes", res_job)

    def test_rich_whatsapp_template_sections(self):
        """Asserts hackathon and job drafts include expected section headers."""
        hack_item = {
            "category": "hackathon",
            "title": "Agentic AI Hackathon",
            "summary": "Build collaborative multi-agent autonomous tools.",
            "skill_tags": "Python, LLM, LangChain",
            "eligibility": "Open to all students",
            "prize": "₹10,00,000",
            "date_str": "2026-11-20",
            "venue": "T-Hub Phase 2",
            "url": "https://devpost.com/hackathon"
        }
        draft_hack = formatter.format_whatsapp_draft(hack_item)
        self.assertIn("📝 *Overview:*", draft_hack)
        self.assertIn("🛠️ *Tracks / Tech:*", draft_hack)
        self.assertIn("🏆 *Prizes & Perks:*", draft_hack)
        self.assertIn("🔗 *Register Here:*", draft_hack)

        job_item = {
            "category": "job",
            "title": "AI Research Intern",
            "summary": "Work on state of the art diffusion and language models.",
            "skill_tags": "Python, PyTorch",
            "compensation": "₹50,000 / month",
            "experience_level": "FRESHER_INTERN",
            "date_str": "2026-11-30",
            "venue": "Hyderabad",
            "url": "https://greenhouse.io/job"
        }
        draft_job = formatter.format_whatsapp_draft(job_item)
        self.assertIn("🏢 *Role Overview:*", draft_job)
        self.assertIn("🛠️ *Tech Stack:*", draft_job)
        self.assertIn("💰 *CTC / Stipend:*", draft_job)
        self.assertIn("🔗 *Apply Direct:*", draft_job)

    def test_senior_job_rejection(self):
        """Asserts job titles with Senior, Lead, Principal are filtered with senior_role_filtered."""
        senior_roles = [
            "Senior Software Engineer",
            "Lead Backend Developer",
            "Principal Platform Architect",
            "Staff Machine Learning Engineer",
            "Engineering Director",
        ]
        for role in senior_roles:
            item = {
                "title": role,
                "summary": "Looking for deep cloud and distributed systems architecture experience.",
                "category": "job",
                "type": "job",
                "link": "https://remoteok.com/job"
            }
            decision, reason, _ = quality_gate.gate(item)
            self.assertEqual(decision, "reject", f"Failed for role: {role}")
            self.assertEqual(reason, "senior_role_filtered", f"Reason was: {reason} for {role}")

    def test_workshop_vs_event_split(self):
        """Asserts items with 'hands-on' or 'workshop' in title or summary are classified as workshops."""
        ws1 = {
            "title": "Hands-on PyTorch and Deep Learning Workshop",
            "summary": "Join us for an interactive training session on neural networks.",
            "category": "event",
            "link": "https://meetup.com/event1"
        }
        dec1, _, clean1 = quality_gate.gate(ws1)
        self.assertEqual(clean1.get("category"), "workshops")

        ev1 = {
            "title": "Hyderabad Open Source Community Meetup",
            "summary": "Discussion and networking around recent open source tools.",
            "category": "event",
            "link": "https://meetup.com/event2"
        }
        dec2, _, clean2 = quality_gate.gate(ev1)
        self.assertEqual(clean2.get("category"), "events")

    def test_past_date_far_in_past_expires_immediately(self):
        """Dates far in the past (e.g. 100 days ago, or 2024) must expire immediately."""
        past_100_days = (config.now_ist() - timedelta(days=100)).strftime("%Y-%m-%d")
        id_past = db.insert_item({
            "source": "Devpost",
            "category": "hackathon",
            "title": "Old 2024 Past Hackathon",
            "url": "https://devpost.com/hackathons/past-100-days",
            "deadline": past_100_days,
            "status": "accepted"
        }, self.db_path)
        self.assertIsNotNone(id_past)

        item = db.get_item(id_past, self.db_path)
        self.assertEqual(item["status"], "expired")
        self.assertTrue(item["expires_at"] < config.now_ist().strftime("%Y-%m-%d %H:%M:%S"))

        posts = pipeline.export_posts_json(db_path=self.db_path, output_path=self.posts_path)
        post_urls = [p["link"] for p in posts]
        self.assertNotIn("https://devpost.com/hackathons/past-100-days", post_urls)

    def test_safe_migration_adds_missing_columns(self):
        """Safe database migration ALTER TABLE only if columns are missing."""
        migration_db = os.path.join(self.temp_dir.name, "migration_test.db")
        # Create legacy table with missing columns
        with db.get_db_connection(migration_db) as conn:
            with conn:
                conn.execute("""
                    CREATE TABLE items (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        source TEXT,
                        category TEXT,
                        title TEXT,
                        url TEXT UNIQUE
                    );
                """)
        # Run init_db which should safely run ALTER TABLE for all new columns
        db.init_db(migration_db)

        with db.get_db_connection(migration_db) as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(items);")
            cols = {row["name"] for row in cursor.fetchall()}
            for expected_col in (
                "first_seen", "event_start", "event_end", "deadline", "expires_at",
                "status", "city", "mode", "fingerprint", "organizer", "venue", "fee", "prize"
            ):
                self.assertIn(expected_col, cols)

    def test_update_item_recomputes_expiry_and_preserves_first_seen(self):
        """db.update_item updates fields, recomputes expires_at, and preserves first_seen."""
        old_seen = "2026-11-01 10:00:00"
        future_dl = (config.now_ist() + timedelta(days=10)).strftime("%Y-%m-%d")
        row_id = db.insert_item({
            "source": "Devpost",
            "category": "hackathon",
            "title": "Initial Hackathon",
            "url": "https://devpost.com/update-test",
            "first_seen": old_seen,
            "deadline": future_dl,
            "status": "accepted"
        }, self.db_path)

        orig = db.get_item(row_id, self.db_path)
        self.assertEqual(orig["first_seen"], old_seen)

        # Update deadline to yesterday -> should immediately become expired
        yesterday_str = (config.now_ist() - timedelta(days=1)).strftime("%Y-%m-%d")
        updated = db.update_item(row_id, {"deadline": yesterday_str}, self.db_path)
        self.assertTrue(updated)

        after = db.get_item(row_id, self.db_path)
        self.assertEqual(after["first_seen"], old_seen)  # Never overwritten
        self.assertEqual(after["status"], "expired")

    def test_export_feed_html_excludes_expired_and_rejected_items(self):
        """Dashboard HTML export must only include unexpired active items."""
        yesterday_str = (config.now_ist() - timedelta(days=1)).strftime("%Y-%m-%d")
        future_str = (config.now_ist() + timedelta(days=10)).strftime("%Y-%m-%d")

        db.insert_item({
            "source": "Devpost",
            "category": "hackathon",
            "title": "Expired HTML Event",
            "url": "https://example.com/expired-html",
            "deadline": yesterday_str,
            "status": "expired"
        }, self.db_path)

        db.insert_item({
            "source": "Devpost",
            "category": "hackathon",
            "title": "Active Future HTML Event",
            "url": "https://example.com/active-html",
            "deadline": future_str,
            "status": "accepted"
        }, self.db_path)

        html_out = os.path.join(self.temp_dir.name, "test_feed_out.html")
        base_html = """<!DOCTYPE html><html><body><script>
        var SEED=[];
        var TODAY="2026-11-09";
        </script></body></html>"""
        template_file = os.path.join(self.temp_dir.name, "tpl.html")
        with open(template_file, "w", encoding="utf-8") as f:
            f.write(base_html)

        res_html = pipeline.export_feed_html(db_path=self.db_path, template_path=template_file, output_path=html_out)
        self.assertNotIn("Expired HTML Event", res_html)
        self.assertIn("Active Future HTML Event", res_html)

    def test_source_health_consecutive_zero_runs_and_alert(self):
        """Source health tracks consecutive zero runs and triggers Telegram alert on 3 failures."""
        with patch("telegram_bot.send_health_alert") as mock_alert:
            # Run 1: 0 items
            z1 = db.update_source_health("TestSource", 0, self.db_path)
            self.assertEqual(z1, 1)

            # Run 2: 0 items
            z2 = db.update_source_health("TestSource", 0, self.db_path)
            self.assertEqual(z2, 2)

            # Run 3: 0 items -> alerts
            z3 = db.update_source_health("TestSource", 0, self.db_path)
            self.assertEqual(z3, 3)

            # Run 4: returns items -> resets to 0
            z4 = db.update_source_health("TestSource", 5, self.db_path)
            self.assertEqual(z4, 0)


if __name__ == "__main__":
    unittest.main()



