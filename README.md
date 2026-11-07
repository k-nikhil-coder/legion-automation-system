# 🛡️ Legion Automation System

Automated opportunity discovery, deduplication, house-style formatting, and multi-channel community curation pipeline for **Legion**.

The system collects hackathons, remote jobs, tech events, learning resources, and developer news from reliable public sources, cleans and formats them into Legion's standardized WhatsApp post formats, and stages them for admin review via Telegram approval bots or a local Markdown review queue.

---

## 🏗️ Architecture & Component Overview

```
                      ┌───────────────────────────────────────────────┐
                      │    Antigravity Scheduled Sidecar              │
                      │    CRON_TZ=Asia/Kolkata 0 */6 * * *           │
                      └───────────────────────┬───────────────────────┘
                                              │ Triggers every 6 hours
                                              ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                                     collectors.py                                      │
│  ┌──────────────────┬─────────────────┬──────────────────┬───────────────────────────┐ │
│  │ Devpost / Dev.to │   Hacker News   │ Reddit / FCC     │ WeWorkRemotely / RemoteOK │ │
│  │ (Hackathons)     │   (Tech News)   │ (Courses)        │ (Jobs & Internships)      │ │
│  └──────────────────┴─────────────────┴──────────────────┴───────────────────────────┘ │
└─────────────────────────────────────────────┬──────────────────────────────────────────┘
                                              │ Raw Items
                                              ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                                     pipeline.py                                        │
│  1. Deduplicate URLs against SQLite Database (db.py)                                   │
│  2. Format new items into Legion Community House Style (formatter.py)                  │
│  3. Insert into database with status = 'pending'                                       │
│  4. Export structured review digest to PENDING_DRAFTS.md                               │
└───────────────────────┬──────────────────────┬──────────────────────┬──────────────────┘
                        │                      │                      │
                        ▼                      ▼                      ▼
       ┌─────────────────────────────────┐   ┌──────────────────┐   ┌─────────────────────────────────┐
       │         telegram_bot.py         │   │ legion_feed.html │   │        PENDING_DRAFTS.md        │
       │ Inline Approval Keyboard:       │   │ Interactive Web: │   │ Local Markdown Review Queue:    │
       │  [✅ Approve]   [❌ Reject]      │   │  - 1-Tap WA Copy │   │  - Formatted Copy-Ready Blocks  │
       │  [📋 Mark Done] [✏️ Edit Draft]  │   │  - Filter/Search │   │  - Direct CLI Command Links     │
       └────────────────┬────────────────┘   └────────┬─────────┘   └────────────────┬────────────────┘
                        │                             │                              │
                        └─────────────────────────────┼──────────────────────────────┘
                                                      │ Admin Approval & 1-Tap Copy
                                                      ▼
                                     ┌─────────────────────────────────┐
                                     │  Legion WhatsApp Groups         │
                                     │   🏆 Hackathons                 │
                                     │   💼 Jobs & Internships         │
                                     │   📅 Events & Workshops         │
                                     │   📚 Courses and Resources      │
                                     │   🌐 Tech Updates & NEWS        │
                                     └─────────────────────────────────┘
```

### Module Breakdown

- **[`config.py`](file:///c:/Users/kunbh/Legion%20Automation%20System/config.py)**: Loads `.env` without external dependencies. Centralizes paths, timeouts, feed definitions, user-agent, and Telegram tokens.
- **[`db.py`](file:///c:/Users/kunbh/Legion%20Automation%20System/db.py)**: SQLite interface managing the `items` table, unique URL index for deduplication, status queries (`pending`, `approved`, `rejected`, `posted`), and summary stats.
- **[`formatter.py`](file:///c:/Users/kunbh/Legion%20Automation%20System/formatter.py)**: Converts raw metadata into Legion's house post templates with standardized emoji headers, fields, and WhatsApp group mappings.
- **[`collectors.py`](file:///c:/Users/kunbh/Legion%20Automation%20System/collectors.py)**: Standard-library HTTP and XML/JSON collectors with automated fallback sources (e.g. Devpost -> Dev.to, Reddit -> FreeCodeCamp, Legion Feed HTML parser) and timeout resilience.
- **[`pipeline.py`](file:///c:/Users/kunbh/Legion%20Automation%20System/pipeline.py)**: Orchestrator that coordinates collection, deduplication, formatting, database insertion, and dual markdown/HTML digest generation.
- **[`telegram_bot.py`](file:///c:/Users/kunbh/Legion%20Automation%20System/telegram_bot.py)**: Telegram approval bot supporting inline button workflows, message updates, `/status`, `/pending`, `/push`, `/feed`, `/web` commands, and terminal CLI fallback options.
- **[`PENDING_DRAFTS.md`](file:///c:/Users/kunbh/Legion%20Automation%20System/PENDING_DRAFTS.md)**: Live generated review queue grouped by category with copyable post blocks.
- **[`legion_feed.html`](file:///c:/Users/kunbh/Legion%20Automation%20System/legion_feed.html)**: Interactive browser-based feed dashboard with category filters, search, bookmarking, and 1-tap WhatsApp clipboard copying.

---


## 🚀 Quickstart & Setup

### 1. Prerequisites
- Python 3.10+ (Standard library only; zero mandatory third-party pip dependencies required).

### 2. Configuration (`.env`)
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
Edit `.env` with your settings:
```ini
# Optional Telegram approval bot (leave blank to use PENDING_DRAFTS.md)
TELEGRAM_BOT_TOKEN=123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ
TELEGRAM_CHAT_ID=123456789

# Collector & Request Settings
HTTP_TIMEOUT=10
MAX_ITEMS_PER_FEED=10
```

### 3. Run the Pipeline
To fetch the latest items, deduplicate, and generate `PENDING_DRAFTS.md`:
```bash
python pipeline.py
```

Options:

- `python pipeline.py --limit 5`: Collect up to 5 items per feed.
- `python pipeline.py --export-only`: Regenerate `PENDING_DRAFTS.md` and `legion_feed.html` from database without fetching feeds.
- `python pipeline.py --import-feed`: Ingest curated opportunities from `legion_feed.html` (or `Downloads\Legion Feed.html`) into SQLite.
- `python pipeline.py --export-html`: Rebuild and sync the `legion_feed.html` dashboard with current database opportunities.
- `python pipeline.py --open`: Open `legion_feed.html` directly in your default web browser.
- `python pipeline.py --serve 8000`: Launch a lightweight local HTTP server for the Legion Feed web dashboard at `http://localhost:8000`.

---

## 🌐 Interactive Web Dashboard (`legion_feed.html`)

The system includes a zero-dependency, self-contained interactive web application: [`legion_feed.html`](file:///c:/Users/kunbh/Legion%20Automation%20System/legion_feed.html).

### Features
1. **Interactive Category Filtering**: Toggle between `All`, `Jobs`, `Hackathons`, `Events`, `Courses`, `Tech News`, and `★ Saved`.
2. **Instant Search**: Real-time filtering across opportunity titles, summaries, sources, and locations.
3. **1-Tap WhatsApp Copy**: Formats the post with standard Legion Community house style emojis (`⚡` Hackathon, `💼` Job, `🗓️` Event, `🎓` Course, `💡` News), date, location, and apply link, directly copied to clipboard.
4. **Countdown Badges**: Automatically highlights opportunities closing soon (`today`, `X days left`).
5. **"+ Add Post" Dialog**: Allows community admins to manually add bespoke posts directly in the browser, stored locally in `localStorage`.
6. **Dark / Light Mode**: Built-in responsive theme toggle with system preference detection.

### Viewing & Managing the Dashboard
- **Direct file opening**:
  ```bash
  python pipeline.py --open
  # or
  python telegram_bot.py --web
  ```
- **Local HTTP server**:
  ```bash
  python pipeline.py --serve 8000
  ```
- **Syncing & Ingesting**:
  ```bash
  # Ingest curated feed posts into SQLite database
  python pipeline.py --import-feed

  # Refresh HTML dashboard with latest database items
  python pipeline.py --export-html
  ```

---


## 📲 Telegram Approval Bot Workflow

The Telegram bot allows community managers to review drafts directly from Telegram using inline buttons.

### Starting the Bot
```bash
# Push pending items to Telegram and listen for admin button clicks
python telegram_bot.py

# Push pending drafts to Telegram and immediately exit
python telegram_bot.py --push

# Start polling listener only
python telegram_bot.py --poll
```

### Telegram Bot Commands
- `/status` — View current counts of pending, approved, rejected, and posted items.
- `/pending` — Show pending item count and queue size.
- `/push` — Send the next batch of pending drafts to the chat with approval buttons.
- `/edit <ID> <new text>` — Edit and update draft text for an item directly from Telegram.
- `/refresh` — Regenerate `PENDING_DRAFTS.md`.

### Inline Keyboard Buttons
Each draft card in Telegram includes interactive buttons:
1. **`[✅ Approve]`**: Marks item as `approved` in SQLite, refreshes `PENDING_DRAFTS.md`, and shows WhatsApp group mapping.
2. **`[❌ Reject]`**: Marks item as `rejected` in SQLite, removing it from the pending review queue.
3. **`[✏️ Edit Draft]`**: Displays the current draft and provides quick `/edit <ID> <text>` instructions.
4. **`[🚀 Mark Done / Posted]`**: Marks item as `posted` once forwarded to WhatsApp.
5. **`[📋 Copy Draft to Clipboard]`**: 1-tap instant clipboard copy on modern Telegram clients (with `<pre>` code block fallback).

---

## 💻 Local CLI / Markdown Fallback Review

If Telegram credentials are not configured, the system operates seamlessly via local files:

### 1. Inspecting the Markdown Digest
Open [`PENDING_DRAFTS.md`](file:///c:/Users/kunbh/Legion%20Automation%20System/PENDING_DRAFTS.md). Items are grouped by category with ready-to-copy text boxes, original links, and quick commands.

### 2. Interactive Terminal Review
Review pending drafts interactively in your terminal:
```bash
python telegram_bot.py --review
```
Options per item: `[a]`pprove, `[r]`eject, `[p]`osted, `[e]`dit, `[s]`kip, `[q]`uit.

### 3. CLI Direct Status Updates & Edits
```bash
# Approve, reject, or mark as posted
python telegram_bot.py --approve <ITEM_ID>
python telegram_bot.py --reject <ITEM_ID>
python telegram_bot.py --posted <ITEM_ID>

# Edit a draft via CLI (with inline text or interactive multi-line prompt)
python telegram_bot.py --edit <ITEM_ID> --text "New formatted draft text..."
python telegram_bot.py --edit <ITEM_ID>

# Check queue statistics
python telegram_bot.py --status
```

---

## 📋 WhatsApp Manual Forwarding Protocol

Legion community posts are published across dedicated WhatsApp groups. Community admins should follow this manual forwarding protocol:

### Target Group Mapping

| Category | Target WhatsApp Group | Post Format Icon |
| :--- | :--- | :--- |
| **Hackathons** | `🏆 Hackathons` | `⚡` |
| **Jobs & Internships** | `💼 Jobs & Internships` | `💼` |
| **Events & Workshops** | `📅 Events & Workshops` | `🗓️` |
| **Courses & Resources** | `📚 Courses and Resources` | `🎓` |
| **Tech News & Updates** | `🌐 Tech Updates & NEWS` | `💡` |

### Step-by-Step Moderation Protocol
1. **Review**: Check the draft in Telegram or [`PENDING_DRAFTS.md`](file:///c:/Users/kunbh/Legion%20Automation%20System/PENDING_DRAFTS.md). Verify that the link works and the opportunity is relevant to the Legion community.
2. **Approve**: Click `[✅ Approve]` in Telegram or run `python telegram_bot.py --approve <ID>`.
3. **Copy Draft**: Copy the standardized formatted text block.
4. **Post to WhatsApp**: Open WhatsApp, go to the corresponding target group (e.g. `🏆 Hackathons`), and paste the text as a new message.
5. **Mark Done**: In Telegram, click `[🚀 Copied & Posted to WhatsApp]` or run `python telegram_bot.py --posted <ID>` to update the database state to `posted`.

---

## ⚙️ Antigravity Scheduled Automation

The pipeline is automated to run every 6 hours via an Antigravity Sidecar configuration:

- **Location**: `C:\Users\kunbh\.gemini\config\sidecars\legion-content-pipeline\sidecar.json`
- **Schedule**: `CRON_TZ=Asia/Kolkata 0 */6 * * *` (Every 6 hours at 00:00, 06:00, 12:00, 18:00 IST)
- **Action**: Initiates an automated conversation to execute `pipeline.py`, deduplicate, insert new opportunities into SQLite, format drafts, and update the review queue.
- **Permissions (`agent_permissions`)**:
  - `workspace_uris`: `["file:///c:/Users/kunbh/Legion Automation System"]`
  - `access_grants`: `["command(python pipeline.py)", "command(python)"]`

### Configuration Specification (`sidecar.json`)
```json
{
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
```

### Enabling the Sidecar Automation
1. Open the Automations Dashboard: `sidecar://dashboard`
2. Locate **Legion Content Pipeline** (`legion-content-pipeline`).
3. Turn on the toggle switch to enable recurring execution.

---

## 🧪 Testing & Verification

Run the test suite using Python's standard `unittest` framework:
```bash
python -m unittest discover tests -v
```
Tests cover:
- SQLite schema, indexing, unique constraints, and status updates.
- House-style draft formatting and WhatsApp category mapping.
- XML/Atom feed parsing, HTML stripping, and error resilience.
- Pipeline execution, deduplication filtering, and markdown generation.
- Telegram client API request framing and callback handling.
- Sidecar configuration schema and permission validation.
