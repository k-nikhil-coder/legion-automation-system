"""
Legion Automation System - Post Formatter
Formats raw collected items into the standardized Legion house style.
"""

import re
import sys
from typing import Dict, Any, Optional

import config
import db

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

def clean_text(text: str, max_len: int = 220) -> str:
    """Cleans up text, strips newlines, and truncates if necessary (max 220 chars)."""
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
        
def is_online_accessible(item: Optional[Dict[str, Any]]) -> bool:
    """Checks if opportunity indicates online, remote, or virtual accessibility."""
    if not isinstance(item, dict):
        return False
    location = _safe_str(item.get("location") or item.get("where"), "")
    title = _safe_str(item.get("title"), "")
    summary = _safe_str(item.get("summary") or item.get("sum"), "")
    combined = f"{location} {title} {summary}".lower()
    remote_markers = (
        "online", "remote", "virtual", "zoom", "google meet", "webinar", "livestream",
        "youtube", "global", "national", "pan-india", "pan india", "all india",
        "across india", "self-paced", "worldwide", "anywhere", "hybrid"
    )
    return any(m in combined for m in remote_markers)

def detect_bengaluru_venue(text: Any) -> Optional[str]:
    """
    Detects specific Bengaluru tech neighborhoods, parks, or hubs.
    """
    if not text:
        return None
    s = str(text).lower()
    if re.search(r'\bkoramangala\b', s):
        return "Koramangala"
    if re.search(r'\bindiranagar\b', s):
        return "Indiranagar"
    if re.search(r'\bwhitefield\b', s):
        return "Whitefield"
    if re.search(r'\bhsr(?:\s+layout)?\b', s):
        return "HSR Layout"
    if re.search(r'\belectronic\s+city\b', s):
        return "Electronic City"
    if re.search(r'\bmanyata\b', s):
        return "Manyata Tech Park"
    if re.search(r'\bbagmane\b', s):
        return "Bagmane Tech Park"
    if re.search(r'\bbellandur\b', s):
        return "Bellandur"
    if re.search(r'\bmg\s+road\b', s):
        return "MG Road"
    return None

def is_location_allowed(item: Dict[str, Any]) -> bool:
    """
    Filters items based on location routing rules:
    - Retain Hyderabad in-person/offline opportunities.
    - Retain Bengaluru in-person/offline opportunities (when tagged or confirmed).
    - Retain Online/Remote/National opportunities accessible from India/Hyderabad.
    - Discard opportunities explicitly located in other cities (Bangalore, Delhi, Mumbai, etc.)
      unless they are online/remote or national events with remote participation.
    """
    if not isinstance(item, dict):
        return True

    # If item is explicitly tagged for Bengaluru
    if item.get("city") == "bengaluru":
        return True
        
    if is_hyderabad_offline(item) or item.get("city") == "hyderabad":
        return True
        
    location = _safe_str(item.get("location") or item.get("where"), "")
    title = _safe_str(item.get("title"), "")
    summary = _safe_str(item.get("summary") or item.get("sum"), "")
    combined = f"{location} {title} {summary}".lower()
    
    is_accessible_online_or_national = is_online_accessible(item)
    
    # Check if explicitly in a disallowed other city
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

def get_location_badge(item: Optional[Dict[str, Any]]) -> str:
    """
    Returns the badge set by quality_gate / enricher: ONLINE, HYDERABAD - OFFLINE, BENGALURU - OFFLINE, or none ("").
    """
    if item is None:
        return "[ONLINE]"
    if not isinstance(item, dict):
        return ""
    if not item:
        return "[ONLINE]"
    if "badge" in item:
        b = item.get("badge")
        if not b or str(b).lower() in ("none", ""):
            return ""
        b_str = str(b).strip().upper()
        if b_str in ("HYDERABAD - OFFLINE", "[HYDERABAD - OFFLINE]"):
            return "[HYDERABAD - OFFLINE]"
        if b_str in ("BENGALURU - OFFLINE", "[BENGALURU - OFFLINE]"):
            return "[BENGALURU - OFFLINE]"
        if b_str in ("ONLINE", "[ONLINE]"):
            return "[ONLINE]"
        return ""
    if item.get("city") == "bengaluru":
        return "[BENGALURU - OFFLINE]"
    if is_hyderabad_offline(item) or item.get("city") == "hyderabad":
        return "[HYDERABAD - OFFLINE]"
    return "[ONLINE]"

def format_location_string(item: Optional[Dict[str, Any]]) -> str:
    """
    Formats the location line including location badges and specific venue details.
    """
    if not isinstance(item, dict):
        return "[ONLINE] Online"
        
    badge = get_location_badge(item)
    location = _safe_str(item.get("location") or item.get("where"), "")
    title = _safe_str(item.get("title"), "")
    summary = _safe_str(item.get("summary") or item.get("sum"), "")
    combined = f"{location} {title} {summary}"

    if badge == "[HYDERABAD - OFFLINE]" or is_hyderabad_offline(item):
        venue = detect_hyderabad_venue(combined)
        if venue:
            return f"[HYDERABAD - OFFLINE] Hyderabad ({venue})"
        return "[HYDERABAD - OFFLINE] Hyderabad"

    if badge == "[BENGALURU - OFFLINE]" or item.get("city") == "bengaluru":
        venue = detect_bengaluru_venue(combined)
        if venue:
            return f"[BENGALURU - OFFLINE] Bengaluru ({venue})"
        return "[BENGALURU - OFFLINE] Bengaluru"
        
    loc = _safe_str(item.get("location") or item.get("where"), "")
    if badge == "[ONLINE]":
        if not loc or loc.lower() in ("online", "virtual", "remote", "zoom", "google meet"):
            return "[ONLINE] Online"
        if loc.startswith("[ONLINE]"):
            return loc
        return f"[ONLINE] {loc}"
        
    return loc if loc else "Unspecified"

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
        
    category = _safe_str(item.get("category") or item.get("type"), "news").lower()
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
        summary = clean_text(_safe_str(item.get("summary"), default_summary), 220)
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

def format_whatsapp(item: Dict[str, Any]) -> str:
    """
    Standardized WhatsApp format:
    Max 7 lines, under 400 chars total.
    Includes a line ONLY if the field exists.
    Jobs: Role at Company, stipend, experience, location, apply.
    Adds '⏳ N days left' line when deadline is within 5 days.
    """
    if not isinstance(item, dict):
        item = {}

    category = _safe_str(item.get("category") or item.get("type"), "news").lower()
    title = _safe_str(item.get("title"), "Opportunity")
    url = _safe_str(item.get("apply_url") or item.get("url") or item.get("link"))
    badge = get_location_badge(item)
    badge_str = f" {badge}" if badge and badge not in title else ""

    # Calculate days left if deadline parseable
    days_left = None
    deadline_val = item.get("deadline") or item.get("event_start") or item.get("date_str")
    if deadline_val:
        dt_dl = db.parse_date_ist(deadline_val)
        if dt_dl:
            diff = (dt_dl - config.now_ist()).total_seconds() / 86400.0
            if 0 <= diff <= 5:
                days_left = max(0, int(round(diff)))

    lines = []

    if category == "job":
        role = _safe_str(item.get("role") or title)
        company = _safe_str(item.get("company"))
        if company:
            lines.append(f"💼 *{role} at {company}*{badge_str}")
        else:
            lines.append(f"💼 *{role}*{badge_str}")

        raw_summary = _safe_str(item.get("summary"))
        if raw_summary:
            import enricher
            clean_s = enricher.summarize(raw_summary, max_len=config.SUMMARY_MAX)
            if clean_s:
                lines.append(f"📌 {clean_s}")

        stipend = _safe_str(item.get("stipend") or item.get("salary"))
        if stipend:
            lines.append(f"💰 Stipend: {stipend}")

        experience = _safe_str(item.get("experience"))
        if experience:
            lines.append(f"🎓 Experience: {experience}")

        location = _safe_str(item.get("location") or item.get("venue") or item.get("where"))
        if location:
            lines.append(f"📍 Location: {location}")

        if days_left is not None:
            if days_left == 0:
                lines.append("⏳ Ends today")
            elif days_left == 1:
                lines.append("⏳ 1 day left")
            else:
                lines.append(f"⏳ {days_left} days left")

        if url:
            lines.append(f"🔗 Apply: {url}")

    else:
        icon_map = {
            "hackathon": "⚡",
            "event": "🗓️",
            "course": "🎓",
            "news": "💡"
        }
        icon = icon_map.get(category, "🚀")
        lines.append(f"{icon} *{title}*{badge_str}")

        raw_summary = _safe_str(item.get("summary"))
        if raw_summary:
            import enricher
            clean_s = enricher.summarize(raw_summary, max_len=config.SUMMARY_MAX)
            if clean_s:
                lines.append(f"📌 {clean_s}")

        date_deadline = _safe_str(item.get("deadline") or item.get("event_start") or item.get("date_str"))
        if date_deadline:
            lines.append(f"📅 Date/Deadline: {date_deadline}")

        if days_left is not None:
            if days_left == 0:
                lines.append("⏳ Ends today")
            elif days_left == 1:
                lines.append("⏳ 1 day left")
            else:
                lines.append(f"⏳ {days_left} days left")

        venue = _safe_str(item.get("venue") or (item.get("location") if not is_online_accessible(item) else ""))
        if venue and venue.lower() not in ("online", "virtual"):
            lines.append(f"📍 Venue: {venue}")

        details_parts = []
        fee = _safe_str(item.get("fee"))
        if fee:
            details_parts.append(f"🎟️ Fee: {fee}")
        prize = _safe_str(item.get("prize"))
        if prize:
            details_parts.append(f"🏆 Prize: {prize}")
        eligibility = _safe_str(item.get("eligibility"))
        if eligibility:
            details_parts.append(f"👥 Eligibility: {eligibility}")

        if details_parts:
            lines.append(" | ".join(details_parts))

        if url:
            lines.append(f"🔗 Apply: {url}")

    if len(lines) > 7:
        lines = lines[:7]

    res = "\n".join(lines)
    if len(res) >= 400:
        excess = len(res) - 395
        for i, l in enumerate(lines):
            if l.startswith("📌 "):
                new_len = len(l) - excess - 3
                if new_len > 15:
                    lines[i] = l[:new_len].rstrip() + "..."
                else:
                    lines.pop(i)
                break
        res = "\n".join(lines)
        if len(res) >= 400:
            excess = len(res) - 395
            if len(lines) > 0 and len(lines[0]) > excess + 20:
                lines[0] = lines[0][:len(lines[0]) - excess - 3].rstrip() + "...*"
            res = "\n".join(lines)
        if len(res) >= 400:
            res = res[:396] + "..."

    return res

def get_whatsapp_target_group(category: str) -> str:
    """Returns the matching WhatsApp group name."""
    cat_key = _safe_str(category, "general").lower()
    return CATEGORY_GROUP_MAP.get(cat_key, "General Updates")
