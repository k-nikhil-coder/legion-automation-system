"""
Comprehensive Test Suite for Legion Automation System
Tests db, formatter, collectors, pipeline, telegram_bot, and sidecar configuration.
"""

import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import config
import db
import formatter
import collectors
import pipeline
import telegram_bot

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

    @patch("collectors.fetch_tech_events")
    @patch("collectors.fetch_remoteok_jobs")
    @patch("collectors.fetch_weworkremotely_jobs")
    @patch("collectors.fetch_reddit_courses")
    @patch("collectors.fetch_hacker_news")
    @patch("collectors.fetch_devpost_hackathons")
    def test_collect_all_items_fault_tolerance(
        self, mock_devpost, mock_hn, mock_reddit, mock_wwr, mock_remoteok, mock_events
    ):
        # If one collector throws an uncaught error, others should continue without unmocked network calls
        mock_devpost.side_effect = RuntimeError("Devpost API broke")
        mock_hn.return_value = [{"source": "HN", "category": "news", "title": "Test HN", "url": "https://news.com"}]
        mock_reddit.return_value = [{"source": "Reddit", "category": "course", "title": "ML Course", "url": "https://reddit.com/c"}]
        mock_wwr.return_value = []
        mock_remoteok.return_value = []
        mock_events.return_value = []
        
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

if __name__ == "__main__":
    unittest.main()


