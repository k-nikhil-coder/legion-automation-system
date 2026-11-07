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
    "jaipur", "kochi", "cochin", "chandigarh", "indore", "mysore", "mysuru",
    "nagpur", "bhopal", "trivandrum", "thiruvananthapuram", "coimbatore",
    "visakhapatnam", "vizag", "vijayawada", "surat", "lucknow", "kanpur",
    "patna", "bhubaneswar"
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
    Returns formatted venue string (e.g. 'T-Hub Phase 2', 'IIIT Hyderabad')
    or None if no specific venue recognized.
    Uses regex word boundaries to prevent substrings like 'github' matching 'thub'.
    """
    if not text:
        return None
    s = str(text).lower()
    
    # 1. T-Hub variants (Phase 2 prioritized) - word boundary prevents matching 'github'
    if re.search(r'\b(?:t-hub|thub|t hub)[\s,]*(?:phase[\s-]*(?:2|ii)|2\.0)\b', s):
        return "T-Hub Phase 2"
    if re.search(r'\b(?:t-hub|thub|t hub)\b', s):
        if re.search(r'\b(?:phase[\s-]*(?:2|ii)|2\.0)\b', s):
            return "T-Hub Phase 2"
        return "T-Hub"
        
    # 2. IIIT Hyderabad variants
    if re.search(r'\b(?:international institute of information technology|iiit)\b', s) and (
        "hyderabad" in s or "hyd" in s or "cie" in s or "-h" in s or "iiith" in s or "gachibowli" in s
    ):
        return "IIIT Hyderabad"
    if re.search(r'\b(?:iiit-h|iiith|iiit hyderabad)\b', s):
        return "IIIT Hyderabad"
        
    # 3. WeWork variants (Kondapur, Nanakramguda)
    if re.search(r'\bwework\b', s):
        if "kondapur" in s or "krishe" in s:
            return "WeWork Kondapur"
        if "nanakramguda" in s or "rajapushpa" in s:
            return "WeWork Nanakramguda"
        return "WeWork"
        
    # 4. Tech Parks, Hubs & Co-working
    if re.search(r'\bdevx\b', s):
        return "DevX"
    if re.search(r'\bt-works\b', s):
        return "T-Works"
    if re.search(r'\bpeople tech\b', s):
        return "People Tech"
    if re.search(r'\bepam\b', s):
        return "EPAM"
    if re.search(r'\bcbit\b', s):
        return "CBIT"
    if re.search(r'\bhicc\b', s) or re.search(r'\bhyderabad international convention centre\b', s):
        return "HICC"
    if re.search(r'\bhitex\b', s):
        return "HITEX"
    if re.search(r'\bmindspace\b', s):
        return "Mindspace Madhapur"
    if re.search(r'\bknowledge city\b', s) or re.search(r'\bsalarpuria sattva\b', s):
        return "Knowledge City"
    if re.search(r'\bcyber towers\b', s):
        return "Cyber Towers"
        
    # 5. Major Hyderabad Tech Neighborhoods & Hubs
    if re.search(r'\bhitec(?:h)?\s+city\b', s) or re.search(r'\bcyberabad\b', s):
        return "Hitec City"
    if re.search(r'\bfinancial\s+district\b', s):
        return "Financial District"
    if re.search(r'\bgachibowli\b', s):
        return "Gachibowli"
    if re.search(r'\bkondapur\b', s):
        return "Kondapur"
    if re.search(r'\bmadhapur\b', s):
        return "Madhapur"
    if re.search(r'\bnanakramguda\b', s):
        return "Nanakramguda"
    if re.search(r'\bbanjara\s+hills\b', s):
        return "Banjara Hills"
    if re.search(r'\bjubilee\s+hills\b', s):
        return "Jubilee Hills"
    if re.search(r'\bbegumpet\b', s):
        return "Begumpet"
    if re.search(r'\bsecunderabad\b', s):
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
    
    is_explicit_online = any(term in location.lower() for term in ("online", "virtual", "zoom", "google meet", "youtube", "livestream"))
    has_hyd_mention = "hyderabad" in combined or bool(re.search(r'\bhyd\b', combined))
    venue = detect_hyderabad_venue(combined)
    has_venue = venue is not None
    
    if not (has_hyd_mention or has_venue):
        return False
        
    if is_explicit_online:
        # If location is explicitly virtual, it is only considered Hyderabad offline if:
        # 1. A physical venue is also identified (e.g., hybrid at T-Hub), OR
        # 2. Location string itself confirms hybrid/in-person (e.g. "Online / In-Person"), OR
        # 3. Explicit in-person attendance phrases appear (and NOT negated like "no in-person" or "no offline venue")
        has_explicit_in_person = bool(
            re.search(r'\b(?:hybrid|in-person|in person|walk-in)\b', location.lower()) or
            (
                re.search(r'\b(?:in-person|in person|attend in person|join in person|physical attendance|walk-in)\b', combined)
                and not re.search(r'\b(?:no|without)\s+(?:in-person|physical|offline)\b', combined)
            )
        )
        if not has_venue and not has_explicit_in_person:
            return False

    return True

def is_location_allowed(item: Dict[str, Any]) -> bool:
    """
    Filters items based on location routing rules:
    - Retain Hyderabad in-person/offline opportunities.
    - Retain Online/Remote/National opportunities accessible from India/Hyderabad.
    - Discard opportunities explicitly located in other cities (Bangalore, Delhi, Mumbai, etc.)
      unless they are online/remote or national events with remote participation.
    """
    if not isinstance(item, dict):
        return True
        
    if is_hyderabad_offline(item):
        return True
        
    location = _safe_str(item.get("location") or item.get("where"), "")
    title = _safe_str(item.get("title"), "")
    summary = _safe_str(item.get("summary") or item.get("sum"), "")
    combined = f"{location} {title} {summary}".lower()
    
    # Check if remote / virtual / national participation is explicitly present
    remote_markers = (
        "online", "remote", "virtual", "zoom", "google meet", "webinar", "livestream",
        "youtube", "global", "national", "pan-india", "pan india", "all india",
        "across india", "self-paced", "worldwide", "anywhere", "hybrid"
    )
    is_accessible_online_or_national = any(m in combined for m in remote_markers)
    
    # Check if explicitly in a disallowed other city
    # Check in location field, in title, or in combined with location markers
    has_disallowed_city = False
    for city in DISALLOWED_OTHER_CITIES:
        pattern = r'\b' + re.escape(city) + r'\b'
        if re.search(pattern, location.lower()) or re.search(pattern, title.lower()):
            has_disallowed_city = True
            break
        if re.search(r'\b(?:in|at|offline|venue|chapter|campus|hub)\s+' + re.escape(city) + r'\b', combined):
            has_disallowed_city = True
            break

    if has_disallowed_city:
        return is_accessible_online_or_national

    return True

def get_location_badge(item: Dict[str, Any]) -> str:
    """Returns '[HYDERABAD - OFFLINE]' for Hyderabad offline items, '[ONLINE]' otherwise."""
    return "[HYDERABAD - OFFLINE]" if is_hyderabad_offline(item) else "[ONLINE]"

def format_location_string(item: Dict[str, Any]) -> str:
    """
    Formats the location line including location badges and specific venue details.
    Examples:
      - [HYDERABAD - OFFLINE] Hyderabad (T-Hub Phase 2)
      - [HYDERABAD - OFFLINE] Hyderabad
      - [ONLINE] Online
    """
    if not isinstance(item, dict):
        return "[ONLINE] Online"
        
    if is_hyderabad_offline(item):
        location = _safe_str(item.get("location") or item.get("where"), "")
        title = _safe_str(item.get("title"), "")
        summary = _safe_str(item.get("summary") or item.get("sum"), "")
        combined = f"{location} {title} {summary}"
        venue = detect_hyderabad_venue(combined)
        if venue:
            return f"[HYDERABAD - OFFLINE] Hyderabad ({venue})"
        return "[HYDERABAD - OFFLINE] Hyderabad"
        
    loc = _safe_str(item.get("location") or item.get("where"), "Online")
    if loc.lower() in ("online", "virtual", "remote", "zoom", "google meet", ""):
        return "[ONLINE] Online"
    if loc.startswith("[ONLINE]") or loc.startswith("[HYDERABAD"):
        return loc
    return f"[ONLINE] {loc}"

def get_location_tag(item: Dict[str, Any]) -> str:
    """
    Returns prominent location tag for review cards (Telegram and Markdown).
    Examples:
    '📍 Location: [HYDERABAD - OFFLINE] Hyderabad (T-Hub Phase 2)'
    '📍 Location: [ONLINE] Online'
    """
    if not isinstance(item, dict):
        return "📍 Location: [ONLINE] Online"
        
    loc_str = format_location_string(item)
    return f"📍 Location: {loc_str}"

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
