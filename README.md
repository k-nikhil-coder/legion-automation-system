# 🛡️ Legion Automation System

Automated opportunity discovery, quality filtering, house-style formatting, and multi-channel community curation pipeline for **Legion**.

The system collects hackathons, tech jobs, local meetups, developer courses, and tech news across 34 resilient sources, passes them through a strict quality gate, formats them into concise, 4-line WhatsApp drafts under 200 characters, and surfaces them through an India-first interactive web dashboard and Telegram moderation bot.

---

## 🏗️ Architecture & Component Overview

```
                               ┌────────────────────────────────────────────────────────┐
                               │             Antigravity Scheduled Sidecar              │
                               │             CRON_TZ=Asia/Kolkata 0 */6 * * *           │
                               └───────────────────────────┬────────────────────────────┘
                                                           │ Triggers every 6 hours
                                                           ▼
┌───────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                                 collectors2.py                                                    │
│  34 Resilient Direct APIs & Feeds (Per-source error isolation · Zero brittle scrapers)                           │
│  ┌───────────────────────────┬───────────────────────────┬─────────────────────────┬───────────────────────────┐  │
│  │ Unstop & Devpost          │ RemoteOK & WWR            │ GDG, HydPy, React Hyd   │ Hacker News & Dev.to      │  │
│  │ (Hackathons & Contests)   │ (Tech Jobs & Internships) │ (Hyderabad Communities) │ (Tech News & Discussions) │  │
│  └───────────────────────────┴───────────────────────────┴─────────────────────────┴───────────────────────────┘  │
└──────────────────────────────────────────────────────────┬────────────────────────────────────────────────────────┘
                                                           │ Raw Collected Items
                                                           ▼
┌───────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                                 quality_gate.py                                                   │
│  Strict Junk, Length, and Relevance Filter:                                                                       │
│  • Off-topic reject: JUNK regex (shopping, rugby, 7s, festivals, gold, betting)                                  │
│  • Article reject: ARTICLE regex or text > 1500 chars (prevents tutorials and discussion rants)                  │
│  • Tech keyword validation: TECH regex (AI, ML, Python, React, Cloud, DevOps, etc.)                               │
│  • Domain verification: Whitelisted TRUSTED domains                                                              │
│  • Location router: ONLINE vs. HYDERABAD - OFFLINE (auto-discards other cities unless remote/national)           │
│  • Summary minification: Strictly trims summaries to <= 220 chars                                                │
└──────────────────────────────────────┬───────────────────────────────────┬────────────────────────────────────────┘
                                       │ "accept"                          │ "review"
                                       ▼                                   ▼
┌───────────────────────────────────────────────────────────────────────┐ ┌─────────────────────────────────────────┐
│                               pipeline.py                             │ │             Review Queue                │
│  1. SQLite deduplication (db.py)                                      │ │ Stored with status='review'             │
│  2. Formatting in Legion house style (formatter.py)                   │ │ Exported to PENDING_DRAFTS.md           │
│  3. ThreadingTCPServer on port 8000 with /api/refresh & /posts.json   │ │ for manual admin verification           │
│  4. Background auto-updater daemon thread (every 30m)                 │ └─────────────────────────────────────────┘
└──────────────────┬────────────────────────────────┬───────────────────┘
                   │                                │
                   ▼                                ▼
┌──────────────────────────────────────┐  ┌─────────────────────────────────────────────────────────────────────────┐
│           telegram_bot.py            │  │                     legion_feed.html (Web Dashboard)                    │
│  Inline Admin Approval Keyboard:     │  │  📍 Sticky Metro Hub Switcher (All India, Hyderabad, Bengaluru, etc.)   │
│   [✅ Approve]   [❌ Reject]         │  │  🏷️ Segmented Category Tray (Hackathons, Jobs, Meetups, Courses, News)  │
│   [📋 Mark Done] [✏️ Edit Draft]     │  │  🔴 Pulsing Live Badges (LIVE NOW, TODAY, CLOSING SOON)                 │
│   [📋 1-Tap Copy for WhatsApp]       │  │  ✂️ 3-Line Clamped Cards (No 2000-char text walls or overflow)           │
│                                      │  │  🔄 1-Click "Update Feed" Button (/api/refresh)                         │
│                                      │  │  📅 1-Click "Add to Calendar" (.ics download)                           │
│                                      │  │  📋 1-Tap "Copy for WhatsApp" (Clean 4-line bullet template)            │
└──────────────────┬───────────────────┘  └────────────────────────────────────┬────────────────────────────────────┘
                   │                                                           │
                   └─────────────────────────────┬─────────────────────────────┘
                                                 │ Admin 1-Tap Clipboard Copy
                                                 ▼
                                ┌───────────────────────────────────┐
                                │       Legion WhatsApp Groups      │
                                │   ⚡ Hackathons                    │
                                │   💼 Jobs & Internships           │
                                │   🗓️ Events & Workshops          │
                                │   🎓 Courses and Resources        │
                                │   💡 Tech Updates & NEWS          │
                                └───────────────────────────────────┘
```

---

## 🌟 What's New in Version 2.0

### 1. Robust Collectors (`collectors2.py`)
- **34 Real-Time Sources**: Hacker News (API + RSS), Dev.to (Hackathons, Career, News), Devpost, RemoteOK (API + RSS), WeWorkRemotely, FreeCodeCamp, ArXiv AI, Lobste.rs, TechCrunch, Kaggle, GitHub Trending, and more.
- **Hyderabad Developer Community Feeds**: Built-in tracking for GDG Hyderabad, HydPy, React Hyderabad, AWS User Group Hyderabad, and Hyderabad AI Community.
- **Resilient Network Layer**: Custom User-Agents, SSL certificate verification compatibility (`certifi`), and per-source `try/except` safety blocks so one flaky feed never interrupts the entire pipeline.
- **Built-in Health Diagnostic**: Run `python collectors2.py` anytime to view the live status, response time, and collected item count across all 34 sources in an ASCII table.

### 2. Intelligent Quality Gate (`quality_gate.py`)
- **Zero Junk / Noise**: Eliminates Dubai shopping festivals, rugby sevens tournaments, jewelry sales, raffles, and betting posts before they reach the database.
- **No Discussion Rants or Articles**: Identifies and drops lengthy blog posts, listicles, and Reddit rants (e.g. 14,000-character discussions) that are not actionable opportunities.
- **Automatic Location Routing**: Detects Hyderabad venues (T-Hub Phase 2, IIIT Hyderabad, DevX, WeWork, HICC) and flags them as `[HYDERABAD - OFFLINE]`. Discards in-person opportunities in other cities (Bangalore, Delhi, Mumbai, etc.) unless they offer online/remote accessibility.
- **3-Tier Routing**:
  - `accept` $\rightarrow$ Added to pending draft queue.
  - `review` $\rightarrow$ Added to review list with explicit flag reason (e.g. untrusted domain, location unstated).
  - `reject` $\rightarrow$ Logged with reason and dropped.

### 3. High-Density India-Specific Web UI (`legion_feed.html`)
- **Sticky Metro Hub Switcher**: Instant 1-click filtering between `🇮🇳 All India`, `📍 Hyderabad (📌 HUB)`, and `📍 Bengaluru`.
- **Summary Minification & Line-Clamping**: Cards strictly clamp summaries to 3 lines (`-webkit-line-clamp: 3; display: -webkit-box; overflow: hidden;`) to maintain a clean, compact, mobile-first scanning layout.
- **Live Status & Time-Left Badges**:
  - `🔴 LIVE NOW` (with pulsing red indicator for active hackathons and events).
  - `⚡ TODAY` (amber badge for events happening today).
  - `⏳ X DAYS LEFT` (amber badge for deadlines closing within 5 days).
  - `📍 [HYDERABAD - OFFLINE]`, `📍 [BENGALURU - OFFLINE]`, and `🌐 [ONLINE]` badges.
- **Quick-Action Bottom Bar**:
  - `Open link ↗`: Directly opens the official registration or application link.
  - `📋 Copy for WhatsApp`: Generates a clean 4-line WhatsApp template strictly under 200 characters.
  - `📅 Cal`: Downloads a standard `.ics` iCalendar file on the fly for 1-click addition to Google Calendar, Apple Calendar, or Outlook.
  - `☆ Save`: Local bookmark toggle stored in browser `localStorage`.
- **Interactive "Update Feed" Button**: Sends a background `POST /api/refresh` request, shows an animated spinner, and updates the view live without reloading.
- **Auto-Syncing**: Multi-threaded server runs a background feed collector every 30 minutes, and the client auto-polls `/posts.json` every 3 minutes.

### 4. Post Enricher & Auto-Expiry Engine (`enricher.py` & `db.py`)
- **Multi-Tier Field Extraction**: Prioritized extraction via Schema.org JSON-LD $\rightarrow$ OpenGraph/meta tags $\rightarrow$ Visible text regex.
- **Rate-Limited & Cached**: 1 request per host per second with 24-hour URL caching.
- **Strict Expiry Rules**:
  - Future deadlines/events expire at end-of-day IST, capped at `first_seen + 30 days`.
  - Past dates expire immediately.
  - Missing dates default to `first_seen + 7 days` (TTL).
  - Hackathons missing dates are routed to `review`, never auto-`accepted`.
- **Dual Deduplication**: Primary URL deduplication + secondary fingerprint (`normalized_title + start_date + city`) collapsing cross-platform reposts.
- **Data Purge**: Automatically nulls raw body/summary after 30 days while retaining URL, title, and fingerprint.

---

## 📁 Repository Structure

| File | Purpose |
| :--- | :--- |
| **[`pipeline.py`](file:///c:/Users/kunbh/Legion%20Automation%20System/pipeline.py)** | Core orchestrator, multi-threaded HTTP server (`--serve`), auto-updater, and JSON/markdown exporter. |
| **[`collectors2.py`](file:///c:/Users/kunbh/Legion%20Automation%20System/collectors2.py)** | 34 resilient live collectors, feed parsers, health diagnostics table, and `--out posts.json` export. |
| **[`quality_gate.py`](file:///c:/Users/kunbh/Legion%20Automation%20System/quality_gate.py)** | 3-tier junk, article, keyword, domain trust, and location validation gate (`accept`, `review`, `reject`). |
| **[`formatter.py`](file:///c:/Users/kunbh/Legion%20Automation%20System/formatter.py)** | Formats opportunities into standardized Legion community WhatsApp post templates; Hyderabad venue detector. |
| **[`db.py`](file:///c:/Users/kunbh/Legion%20Automation%20System/db.py)** | SQLite database driver (`legion.db`) with WAL mode, unique URL index, deduplication, and badge tracking. |
| **[`legion_feed.html`](file:///c:/Users/kunbh/Legion%20Automation%20System/legion_feed.html)** | Interactive high-density dashboard with sticky metro hub switcher, category tray, `.ics` calendar generator, and WhatsApp copy. |
| **[`posts.json`](file:///c:/Users/kunbh/Legion%20Automation%20System/posts.json)** | Clean, quality-gated JSON feed exported for frontend rendering (with anti-cache headers). |
| **[`telegram_bot.py`](file:///c:/Users/kunbh/Legion%20Automation%20System/telegram_bot.py)** | Telegram approval bot supporting inline buttons (`Approve`, `Reject`, `Edit`, `Copy`, `Posted`) and terminal review. |
| **[`PENDING_DRAFTS.md`](file:///c:/Users/kunbh/Legion%20Automation%20System/PENDING_DRAFTS.md)** | Auto-generated review queue grouped by category with ready-to-copy post blocks. |
| **[`config.py`](file:///c:/Users/kunbh/Legion%20Automation%20System/config.py)** | Zero-dependency `.env` configuration loader for paths, timeouts, and tokens. |
| **[`tests/test_legion.py`](file:///c:/Users/kunbh/Legion%20Automation%20System/tests/test_legion.py)** | 98 automated unit and integration tests covering collectors, quality gate, server routes, concurrency, and formatting. |

---

## 🚀 Getting Started

### 1. Requirements
- **Python 3.10+** (Standard library only; zero mandatory third-party pip dependencies).
- Optional for tests: `pip install pytest certifi`.

### 2. Configuration
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
Key configuration parameters in `.env`:
```ini
# Server & Dashboard
PORT=8000
AUTO_UPDATE_MINUTES=30

# Telegram Bot (Optional)
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here

# Network Settings
HTTP_TIMEOUT=12
MAX_ITEMS_PER_FEED=10
```

### 3. Launching the Web App
Start the local server with background auto-updating:
```bash
python pipeline.py --serve 8000
```
Then visit:
- **Dashboard**: [http://localhost:8000/](http://localhost:8000/)
- **Live Feed JSON**: [http://localhost:8000/posts.json](http://localhost:8000/posts.json)

---

## 🛠️ CLI Command Reference

### Running Collectors & Diagnostics
```bash
# Run all 34 collectors and display live ASCII health table
python collectors2.py

# Run collectors and export directly to posts.json
python collectors2.py --out posts.json

# Fetch up to 5 items per source
python collectors2.py --limit 5
```

### Pipeline & Feed Management
```bash
# Full collection, deduplication, quality gate, and export
python pipeline.py

# Start multi-threaded server on custom port with 15-minute auto-update
python pipeline.py --serve 8080 --auto-update 15

# Export posts.json and PENDING_DRAFTS.md from database without network fetching
python pipeline.py --export-only

# Open the feed dashboard in your default browser
python pipeline.py --open
```

### Telegram Bot & Terminal Moderation
```bash
# Push pending drafts to Telegram and listen for inline button clicks
python telegram_bot.py

# Push pending drafts to Telegram and immediately exit
python telegram_bot.py --push

# Review pending drafts interactively in your terminal
python telegram_bot.py --review

# Approve, reject, or mark an opportunity as posted
python telegram_bot.py --approve <ID>
python telegram_bot.py --reject <ID>
python telegram_bot.py --posted <ID>

# Check moderation queue statistics
python telegram_bot.py --status
```

---

## 📋 Standard Legion WhatsApp Formats

When an admin clicks **`📋 Copy for WhatsApp`** on any card or in Telegram, the system generates a concise, standardized, 4-line message under 200 characters:

### Hackathons & Competitions (`⚡`)
```text
⚡ *Agentversity AI Hackathon* [HYDERABAD - OFFLINE]

📌 Hyderabad flagship multi-agent AI hackathon. ₹5 Lakhs compute credits.
📅 Date: 20 Nov 2026
📍 Venue: Hyderabad (T-Hub Phase 2)
🔗 Apply: https://unstop.com/hackathons/agentversity-2026
```

### Tech Jobs & Internships (`💼`)
```text
💼 *Senior Machine Learning Engineer at Anthropic* [ONLINE]

📌 Lead model alignment and low-latency inference. Remote across India.
📅 Date: 12 Nov 2026
📍 Venue: Remote / All India
🔗 Apply: https://remoteok.com/remote-jobs/senior-ml-engineer
```

### Local Meetups & Community Events (`🗓️`)
```text
🗓️ *GDG DevFest Hyderabad 2026* [HYDERABAD - OFFLINE]

📌 Annual developer gathering. Sessions on Cloud, Android, and Gemini.
📅 Date: 28 Nov 2026
📍 Venue: Hyderabad (HICC Novotel)
🔗 Apply: https://gdg.community.dev/gdg-hyderabad/
```

### Courses & Learning Resources (`🎓`)
```text
🎓 *Autonomous Agents Architecture – FreeCodeCamp* [ONLINE]

📌 Free 8-hour hands-on curriculum covering tool calling and vector memory.
📍 Venue: Online
🔗 Apply: https://freecodecamp.org/news/autonomous-agents
```

### Tech News & Updates (`💡`)
```text
💡 *Clj.rs – Clojure dialect implemented in Rust* [ONLINE]

📌 Trending on Hacker News (10 points) posted by @simonpure.
📍 Venue: Online
🔗 Apply: https://clj.rs/
```

---

## 🌐 API Endpoints

When running `python pipeline.py --serve 8000`:

| Method | Endpoint | Description | Headers |
| :--- | :--- | :--- | :--- |
| `GET` | `/` | Serves `legion_feed.html`. | `Content-Type: text/html; charset=utf-8` |
| `GET` | `/posts.json` | Returns clean, quality-gated opportunity array. | `Cache-Control: no-cache, no-store, must-revalidate`<br>`Access-Control-Allow-Origin: *` |
| `GET` / `POST` | `/api/refresh` | Triggers thread-safe background collection from 34 sources and exports updated `posts.json`. | `Content-Type: application/json` |

---

## 🧪 Testing & Quality Assurance

Run the test suite using `pytest`:
```bash
pytest -v
```

The 112 test cases cover:
- **Quality Gate**: Keyword regex matchers, junk topic exclusions, length thresholds, domain trust checks, and location classification (`ONLINE`, `HYDERABAD - OFFLINE`, discard other cities).
- **Text Minification**: Guarantees summaries are truncated to $\le 180$ characters and strips anti-spam keywords (e.g. `**BENEVOLENT**`).
- **HTTP Endpoints & Headers**: Validates `/api/refresh`, `/posts.json` anti-caching headers (`no-store`, `no-cache`), and thread-safety concurrency locks.
- **Database Integrity**: Tests WAL mode, indexing, deduplication on unique URLs, and status queries.
- **Collector Resilience**: Simulates network timeouts and malformed responses to ensure zero unhandled exceptions.

---

## ⚙️ Automated 6-Hour Background Schedule

The pipeline is automated to run every 6 hours via Antigravity Sidecar:
- **Configuration**: `C:\Users\kunbh\.gemini\config\sidecars\legion-content-pipeline\sidecar.json`
- **Schedule**: `CRON_TZ=Asia/Kolkata 0 */6 * * *` (00:00, 06:00, 12:00, 18:00 IST)
- **Action**: Executes `python pipeline.py`, deduplicates new opportunities, formats drafts, and refreshes the live feed dashboard.

---

## 🤝 Community Moderation Protocol

1. **Scan Feed**: Open [http://localhost:8000/](http://localhost:8000/) or inspect [`PENDING_DRAFTS.md`](file:///c:/Users/kunbh/Legion%20Automation%20System/PENDING_DRAFTS.md).
2. **Filter by Metro**: Click `📍 Hyderabad` for in-person opportunities or `🇮🇳 All India` for nationwide hackathons and remote roles.
3. **Copy Draft**: Click **`📋 Copy for WhatsApp`** to copy the formatted text block.
4. **Post to WhatsApp**: Paste into the matching WhatsApp group (`⚡ Hackathons`, `💼 Jobs`, `🗓️ Events`, `🎓 Courses`, or `💡 News`).
5. **Mark Done**: Toggle **`☆ Save`** to mark as handled, or approve via Telegram bot.
