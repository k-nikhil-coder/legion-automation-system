"""
Legion Automation System - Configuration Module
Loads environment variables from .env and exposes centralized settings.
"""

import os
from typing import Dict, Any

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def load_env(env_file: str = os.path.join(BASE_DIR, ".env")) -> None:
    """
    Lightweight .env parser using standard library.
    Loads key=value pairs into os.environ if not already present.
    """
    if not os.path.exists(env_file):
        return
    
    try:
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip()
                    # Strip wrapping quotes if present
                    if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                        val = val[1:-1]
                    if key and key not in os.environ:
                        os.environ[key] = val
    except Exception as e:
        print(f"[config] Warning: Failed to load .env: {e}")

# Automatically load on import
load_env()

# Expiry and Localization Constants
SUMMARY_MAX = 450
MAX_SUMMARY_CHARS = 450
TTL_DAYS = 7
MAX_FUTURE_DAYS = 30
PURGE_BODY_DAYS = 30
USE_OLLAMA = int(os.environ.get("USE_OLLAMA", "0"))

# Timezone (Asia/Kolkata - IST)
try:
    from zoneinfo import ZoneInfo
    IST = ZoneInfo("Asia/Kolkata")
except Exception:
    from datetime import timezone, timedelta
    IST = timezone(timedelta(hours=5, minutes=30))

from datetime import datetime

def now_ist() -> datetime:
    """Returns the current datetime in Asia/Kolkata timezone."""
    return datetime.now(IST)

# Cities Configuration
CITIES: Dict[str, Dict[str, Any]] = {
    "hyderabad": {
        "name": "Hyderabad",
        "aliases": [
            "hyderabad", "hyd", "secunderabad", "hitec city", "gachibowli",
            "t-hub", "iiit hyderabad", "hicc"
        ],
        "badge": "HYDERABAD - OFFLINE"
    },
    "bengaluru": {
        "name": "Bengaluru",
        "aliases": [
            "bengaluru", "bangalore", "blr", "koramangala", "indiranagar",
            "whitefield", "hsr layout", "electronic city"
        ],
        "badge": "BENGALURU - OFFLINE"
    }
}

def normalize_city(city_or_text: Any) -> str:
    """
    Normalizes a city string or text mention to a standard city identifier:
    'hyderabad', 'bengaluru', 'online', or 'other'.
    Both 'Bangalore' and 'Bengaluru' map to 'bengaluru'.
    """
    if not city_or_text:
        return "other"
    s = str(city_or_text).lower().strip()
    import re
    for alias in CITIES["bengaluru"]["aliases"]:
        if re.search(r'\b' + re.escape(alias) + r'\b', s):
            return "bengaluru"
    for alias in CITIES["hyderabad"]["aliases"]:
        if re.search(r'\b' + re.escape(alias) + r'\b', s):
            return "hyderabad"
    if any(m in s for m in ("online", "virtual", "remote", "zoom", "google meet")):
        return "online"
    return "other"

# Paths
DB_PATH = os.environ.get("DB_PATH", os.path.join(BASE_DIR, "legion.db"))
PENDING_DRAFTS_PATH = os.environ.get("PENDING_DRAFTS_PATH", os.path.join(BASE_DIR, "PENDING_DRAFTS.md"))
LEGION_FEED_HTML_PATH = os.environ.get("LEGION_FEED_HTML_PATH", os.path.join(BASE_DIR, "legion_feed.html"))
POSTS_JSON_PATH = os.environ.get("POSTS_JSON_PATH", os.path.join(BASE_DIR, "posts.json"))
AUTO_UPDATE_INTERVAL_MINUTES = int(os.environ.get("AUTO_UPDATE_INTERVAL_MINUTES", "30"))


# Telegram Bot Settings
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

# Network & Collector Settings
HTTP_TIMEOUT = int(os.environ.get("HTTP_TIMEOUT", "10"))
MAX_ITEMS_PER_FEED = int(os.environ.get("MAX_ITEMS_PER_FEED", "10"))
HN_API_BASE = os.environ.get("HN_API_BASE", "https://hacker-news.firebaseio.com/v0")
USER_AGENT = os.environ.get(
    "USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 LegionCommunityBot/1.0"
)

# Feed Sources Configuration
# Feed Sources Configuration
FEEDS: Dict[str, Dict[str, Any]] = {
    "hackathons": {
        "primary": "https://devpost.com/hackathons.rss",
        "fallback": "https://dev.to/feed/tag/hackathon",
        "category": "hackathon",
        "source": "Devpost",
        "city": "online"
    },
    "news": {
        "primary": "https://news.ycombinator.com/rss",
        "fallback": "https://techcrunch.com/feed/",
        "category": "news",
        "source": "Hacker News",
        "city": "online"
    },
    "courses": {
        "primary": "https://www.reddit.com/r/learnmachinelearning/hot.rss",
        "fallback": "https://www.freecodecamp.org/news/rss/",
        "category": "course",
        "source": "Reddit r/learnmachinelearning",
        "city": "online"
    },
    "jobs_wwr": {
        "primary": "https://weworkremotely.com/categories/remote-programming-jobs.rss",
        "fallback": "",
        "category": "job",
        "source": "WeWorkRemotely",
        "city": "online"
    },
    "jobs_remoteok": {
        "primary": "https://remoteok.com/api",
        "fallback": "https://remoteok.io/remote-jobs.rss",
        "category": "job",
        "source": "RemoteOK",
        "city": "online"
    },
    "legion_feed": {
        "primary": LEGION_FEED_HTML_PATH,
        "fallback": "",
        "category": "curated",
        "source": "Legion Feed Web App",
        "city": "hyderabad"
    },
    "commudle_hyderabad": {
        "primary": "https://api.commudle.com/api/v1/events?city=Hyderabad",
        "fallback": "https://www.commudle.com/communities",
        "category": "event",
        "source": "Commudle Hyderabad",
        "city": "hyderabad"
    },
    "luma_hyderabad": {
        "primary": "https://api.lu.ma/discover/events?location=Hyderabad",
        "fallback": "https://luma.com/MeetUpsinIndia",
        "category": "event",
        "source": "Luma Hyderabad",
        "city": "hyderabad"
    },
    "meetup_hyderabad": {
        "primary": "https://www.meetup.com/find/?location=in--Hyderabad&source=EVENTS&categoryFilter=technology",
        "fallback": "",
        "category": "event",
        "source": "Meetup Hyderabad",
        "city": "hyderabad"
    },
    "unstop_hackathons": {
        "primary": "https://unstop.com/api/public/opportunity/search-result?opportunity=hackathons&quickApply=true&locations=Hyderabad",
        "fallback": "",
        "category": "hackathon",
        "source": "Unstop Hackathons",
        "city": "hyderabad"
    },
    "devpost_hyderabad": {
        "primary": "https://devpost.com/hackathons?search=Hyderabad",
        "fallback": "",
        "category": "hackathon",
        "source": "Devpost Hyderabad",
        "city": "hyderabad"
    },
    "commudle_bengaluru": {
        "primary": "https://api.commudle.com/api/v1/events?city=Bengaluru",
        "fallback": "https://www.commudle.com/communities",
        "category": "event",
        "source": "Commudle Bengaluru",
        "city": "bengaluru"
    },
    "luma_bengaluru": {
        "primary": "https://api.lu.ma/discover/events?location=Bengaluru",
        "fallback": "https://luma.com/MeetUpsinIndia",
        "category": "event",
        "source": "Luma Bengaluru",
        "city": "bengaluru"
    },
    "gdg_bangalore": {
        "primary": "https://gdg.community.dev/api/chapter/182/event/",
        "fallback": "",
        "category": "event",
        "source": "GDG Bangalore",
        "city": "bengaluru"
    },
    "bangpypers": {
        "primary": "https://www.meetup.com/bangpypers/events/ical/",
        "fallback": "",
        "category": "event",
        "source": "BangPypers",
        "city": "bengaluru"
    },
    "hasgeek": {
        "primary": "https://hasgeek.com/feed",
        "fallback": "",
        "category": "event",
        "source": "Hasgeek",
        "city": "bengaluru"
    }
}

# Hyderabad Event Ecosystem Configurations
COMMUDLE_HYDERABAD_URL = os.environ.get("COMMUDLE_HYDERABAD_URL", FEEDS["commudle_hyderabad"]["primary"])
COMMUDLE_FALLBACK_URL = os.environ.get("COMMUDLE_FALLBACK_URL", FEEDS["commudle_hyderabad"]["fallback"])
LUMA_HYDERABAD_URL = os.environ.get("LUMA_HYDERABAD_URL", FEEDS["luma_hyderabad"]["primary"])
LUMA_INDIA_FEED = os.environ.get("LUMA_INDIA_FEED", FEEDS["luma_hyderabad"]["fallback"])
MEETUP_HYDERABAD_URL = os.environ.get(
    "MEETUP_HYDERABAD_URL",
    "https://www.meetup.com/find/?location=in--Hyderabad&radius=25&source=EVENTS&categoryFilter=technology"
)
UNSTOP_HACKATHONS_URL = os.environ.get("UNSTOP_HACKATHONS_URL", FEEDS["unstop_hackathons"]["primary"])
DEVPOST_HYDERABAD_URL = os.environ.get("DEVPOST_HYDERABAD_URL", FEEDS["devpost_hyderabad"]["primary"])

HYDERABAD_COMMUNITIES = [
    "GDG Hyderabad",
    "HydPy",
    "React Hyderabad",
    "AWS User Group Hyderabad",
    "AI Community Hyderabad"
]

COMMUDLE_COMMUNITY_SLUGS = [
    ("GDG Hyderabad", "gdg-hyderabad"),
    ("HydPy", "hydpy"),
    ("React Hyderabad", "react-hyderabad"),
    ("AWS User Group Hyderabad", "aws-hyderabad"),
    ("AI Community Hyderabad", "ai-hyderabad")
]

HYDERABAD_VENUES = [
    "T-Hub",
    "T-Hub Phase 2",
    "IIIT Hyderabad",
    "IIIT-H",
    "Hitec City",
    "HITEC City",
    "Gachibowli",
    "Kondapur",
    "Madhapur",
    "Nanakramguda",
    "Financial District",
    "DevX",
    "WeWork Kondapur",
    "WeWork Nanakramguda",
    "EPAM",
    "People Tech",
    "CBIT",
    "T-Works",
    "HICC",
    "HITEX",
    "Mindspace",
    "Knowledge City",
    "Cyber Towers"
]


