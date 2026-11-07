"""
Legion Automation System - Post Formatter
Formats raw collected items into the standardized Legion house style.
"""

import re
import sys
from typing import Dict, Any, Optional

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

CATEGORY_GROUP_MAP = {
    "hackathon": "🏆 Hackathons",
    "event": "📅 Events & Workshops",
    "course": "📚 Courses and Resources",
    "job": "💼 Jobs & Internships",
    "news": "🌐 Tech Updates & NEWS"
}

DISALLOWED_OTHER_CITIES = [
    "bangalore", "bengaluru", "delhi", "new delhi", "ncr", "mumbai", "pune",
    "chennai", "kolkata", "noida", "gurugram", "gurgaon", "ahmedabad",
    "jaipur", "kochi", "cochin", "chandigarh", "indore"
]

def _safe_str(val: Any, default: str = "") -> str:
    """Safely converts value to stripped string, using default if None or empty."""
    if val is None:
        return default
    s = str(val).strip()
    return s if s else default

def clean_text(text: str, max_len: int = 140) -> str:
    """Cleans up text, strips newlines, and truncates if necessary."""
    if not text:
        return ""
    clean = " ".join(str(text).replace("\n", " ").replace("\r", " ").split())
    if len(clean) > max_len:
        if max_len <= 3:
            return clean[:max_len]
        return clean[:max_len - 3] + "..."
    return clean

def detect_hyderabad_venue(text: Any) -> Optional[str]:
    """
    Detects specific Hyderabad venues, tech parks, or hubs from text.
    Returns formatted venue string or None if no specific venue recognized.
    """
    if not text:
        return None
    s = str(text).lower()
    
    # T-Hub variants
    if "t-hub phase 2" in s or "thub phase 2" in s or "t-hub 2.0" in s or "thub 2.0" in s:
        return "T-Hub Phase 2, Gachibowli"
    if "t-hub" in s or "thub" in s:
        if "gachibowli" in s or "phase 2" in s:
            return "T-Hub Phase 2, Gachibowli"
        return "T-Hub"
        
    # IIIT Hyderabad variants
    if "iiit" in s and ("hyderabad" in s or "hyd" in s or "cie" in s or "-h" in s or "iiith" in s):
        if "cie" in s or "research park" in s:
            return "IIIT Hyderabad (Research Park & CIE)"
        return "IIIT Hyderabad, Gachibowli"
    if "iiit-h" in s or "iiith" in s or "iiit hyderabad" in s:
        if "cie" in s or "research park" in s:
            return "IIIT Hyderabad (Research Park & CIE)"
        return "IIIT Hyderabad, Gachibowli"
        
    # WeWork variants
    if "wework" in s:
        if "kondapur" in s:
            return "WeWork, Kondapur"
        if "nanakramguda" in s:
            return "WeWork, Nanakramguda"
        if "rajapushpa" in s or "krishe" in s:
            return "WeWork, Kondapur"
        return "WeWork, Hyderabad"
        
    # Prominent tech companies & co-working hubs
    if "devx" in s:
        return "DevX, Hitec City"
    if "people tech" in s:
        return "People Tech, Hyderabad"
    if "epam" in s:
        return "EPAM, Hyderabad"
    if "cbit" in s:
        return "CBIT, Hyderabad"
        
    # Major tech hubs / clusters in Hyderabad
    if "hitec city" in s or "hitech city" in s or "cyberabad" in s:
        return "Hitec City"
    if "financial district" in s:
        return "Financial District, Nanakramguda"
    if "nanakramguda" in s:
        return "Nanakramguda"
    if "gachibowli" in s:
        return "Gachibowli"
    if "kondapur" in s:
        return "Kondapur"
    if "madhapur" in s:
        return "Madhapur"
    if "banjara hills" in s:
        return "Banjara Hills"
    if "jubilee hills" in s:
        return "Jubilee Hills"
    if "begumpet" in s:
        return "Begumpet"
    if "secunderabad" in s:
        return "Secunderabad"
        
    return None

def is_hyderabad_offline(item: Dict[str, Any]) -> bool:
    """
    Determines if an item represents a physical / in-person opportunity in Hyderabad.
    """
    if not isinstance(item, dict):
        return False
        
    location = _safe_str(item.get("location") or item.get("where"), "")
    title = _safe_str(item.get("title"), "")
    summary = _safe_str(item.get("summary") or item.get("sum"), "")
    combined = f"{location} {title} {summary}".lower()
    
    is_explicit_online = any(term in location.lower() for term in ("online", "virtual", "zoom", "google meet", "youtube"))
    has_hyd_mention = "hyderabad" in combined or bool(re.search(r'\bhyd\b', combined))
    has_venue = detect_hyderabad_venue(combined) is not None
    
    if has_hyd_mention or has_venue:
        if is_explicit_online and not any(term in combined for term in ("in-person", "offline", "hybrid", "venue", "walk-in")) and not has_venue:
            return False
        return True
        
    return False

def is_location_allowed(item: Dict[str, Any]) -> bool:
    """
    Filters items based on location routing rules:
    - Retain Hyderabad in-person/offline opportunities.
    - Retain Online/Remote/National opportunities accessible from India/Hyderabad.
    - Discard other cities (Bangalore, Delhi, etc.) unless remote/online or major national event.
    """
    if not isinstance(item, dict):
        return True
        
    if is_hyderabad_offline(item):
        return True
        
    location = _safe_str(item.get("location") or item.get("where"), "")
    title = _safe_str(item.get("title"), "")
    summary = _safe_str(item.get("summary") or item.get("sum"), "")
    combined = f"{location} {title} {summary}".lower()
    
    # Check if remote / online / national participation is possible
    remote_markers = (
        "online", "remote", "virtual", "zoom", "meet", "webinar", "livestream",
        "youtube", "global", "national", "pan-india", "pan india", "all india",
        "across india", "india", "self-paced", "worldwide", "anywhere"
    )
    is_accessible_online_or_national = any(m in combined for m in remote_markers)
    if is_accessible_online_or_national:
        return True
        
    # Check if explicitly in a disallowed other city
    for city in DISALLOWED_OTHER_CITIES:
        if re.search(r'\b' + re.escape(city) + r'\b', combined):
            return False
            
    # Default: allow if not explicitly tied to a disallowed offline city
    return True

def get_location_badge(item: Dict[str, Any]) -> str:
    """Returns '[HYDERABAD - OFFLINE]' for Hyderabad offline items, '[ONLINE]' otherwise."""
    return "[HYDERABAD - OFFLINE]" if is_hyderabad_offline(item) else "[ONLINE]"

def format_location_string(item: Dict[str, Any]) -> str:
    """
    Formats the location line including specific venue details for Hyderabad offline items.
    """
    if not isinstance(item, dict):
        return "Online"
        
    if is_hyderabad_offline(item):
        location = _safe_str(item.get("location") or item.get("where"), "")
        title = _safe_str(item.get("title"), "")
        summary = _safe_str(item.get("summary") or item.get("sum"), "")
        combined = f"{location} {title} {summary}"
        venue = detect_hyderabad_venue(combined)
        if venue:
            return f"Hyderabad ({venue})"
        return "Hyderabad"
        
    loc = _safe_str(item.get("location") or item.get("where"), "Online")
    return loc if loc else "Online"

def get_location_tag(item: Dict[str, Any]) -> str:
    """
    Returns prominent location tag for review cards (Telegram and Markdown).
    Examples:
    '📍 Location: Hyderabad (T-Hub Phase 2, Gachibowli)'
    '📍 Location: Hyderabad (T-Hub)'
    '📍 Location: Online'
    """
    if not isinstance(item, dict):
        return "📍 Location: Online"
        
    if is_hyderabad_offline(item):
        loc_str = format_location_string(item)
        return f"📍 Location: {loc_str}"
        
    loc = _safe_str(item.get("location") or item.get("where"), "Online").lower()
    if loc in ("remote", "wfh", "work from home"):
        return "📍 Location: Remote"
    return "📍 Location: Online"

def format_draft(item: Dict[str, Any]) -> str:
    """
    Formats an item dictionary into the Legion community post template.
    Guaranteed not to crash even if fields contain None or non-string values.
    Includes location badge ([HYDERABAD - OFFLINE] / [ONLINE]) and venue details.
    """
    if not isinstance(item, dict):
        item = {}
        
    category = _safe_str(item.get("category"), "news").lower()
    title = _safe_str(item.get("title"), "Community Update")
    url = _safe_str(item.get("url"), "")
    badge = get_location_badge(item)
    
    # Avoid duplicate badge if already present in title
    badge_suffix = f" {badge}" if badge not in title else ""
    
    if category == "job":
        company = _safe_str(item.get("company"), "Tech Company")
        role = _safe_str(item.get("role"), title)
        exp = _safe_str(item.get("experience"), "Entry / Mid level")
        if is_hyderabad_offline(item):
            loc = format_location_string(item)
        else:
            loc = _safe_str(item.get("location"), "Remote / Hybrid")
        skills = _safe_str(item.get("skills"), "Software Engineering / Tech")
        
        draft = (
            f"💼 {role} - {company}{badge_suffix}\n\n"
            f"📌 Experience: {exp}\n"
            f"📍 Location: {loc}\n"
            f"🎯 Skills: {skills}\n"
            f"🔗 Apply: {url}"
        )
    else:
        icon = "🚀"
        if category == "hackathon":
            icon = "⚡"
        elif category == "course":
            icon = "🎓"
        elif category == "event":
            icon = "🗓️"
        elif category == "news":
            icon = "💡"
            
        default_summary = f"New update from {_safe_str(item.get('source'), 'feed')}"
        summary = clean_text(_safe_str(item.get("summary"), default_summary), 160)
        date_deadline = _safe_str(item.get("deadline") or item.get("date_str"), "Check website")
        location = format_location_string(item)
        eligibility = _safe_str(item.get("eligibility"), "Open to all")
        
        draft = (
            f"{icon} {title}{badge_suffix}\n\n"
            f"📌 What: {summary}\n"
            f"📅 Date / Deadline: {date_deadline}\n"
            f"📍 Where: {location}\n"
            f"🎯 Who: {eligibility}\n"
            f"🔗 {url}"
        )
        
    return draft

def get_whatsapp_target_group(category: str) -> str:
    """Returns the matching WhatsApp group name."""
    cat_key = _safe_str(category, "general").lower()
    return CATEGORY_GROUP_MAP.get(cat_key, "General Updates")
