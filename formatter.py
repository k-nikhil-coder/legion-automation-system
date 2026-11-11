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
    "hackathons": "🏆 Hackathons",
    "event": "📅 Events & Workshops",
    "events": "📅 Events & Workshops",
    "workshop": "📅 Events & Workshops",
    "workshops": "📅 Events & Workshops",
    "course": "📚 Courses and Resources",
    "courses": "📚 Courses and Resources",
    "job": "💼 Jobs & Internships",
    "jobs": "💼 Jobs & Internships",
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
    badge = _safe_str(item.get("badge"), "")
    city = _safe_str(item.get("city"), "")
    mode = _safe_str(item.get("mode"), "")
    combined = f"{location} {title} {summary} {badge} {city} {mode}".lower()
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
    
    if category in ("job", "jobs"):
        company = _safe_str(item.get("company"), "Tech Company")
        role = _safe_str(item.get("role"), title)
        exp = _safe_str(item.get("experience"), "Entry / Mid level")
        if is_hyderabad_offline(item):
            loc = format_location_string(item)
        else:
            loc = _safe_str(item.get("location"), "Remote / Hybrid")
        skills = _safe_str(item.get("skill_tags") or item.get("skills"), "Software Engineering / Tech")
        
        draft = (
            f"💼 {role} - {company}{badge_suffix}\n\n"
            f"📌 Experience: {exp}\n"
            f"📍 Location: {loc}\n"
            f"🎯 Skills: {skills}\n"
            f"🔗 Apply: {url}"
        )
    else:
        icon = "🚀"
        if category in ("hackathon", "hackathons"):
            icon = "⚡"
        elif category in ("course", "courses"):
            icon = "🎓"
        elif category in ("workshop", "workshops"):
            icon = "🛠️"
        elif category in ("event", "events"):
            icon = "🗓️"
        elif category == "news":
            icon = "💡"
            
        default_summary = f"New update from {_safe_str(item.get('source'), 'feed')}"
        summary = clean_text(_safe_str(item.get("summary"), default_summary), config.SUMMARY_MAX)
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

def summarize_with_ollama(item: Dict[str, Any], timeout: float = 4.0) -> Optional[str]:
    """
    Summarizes opportunity with local Ollama instance if USE_OLLAMA=1.
    Falls back to None on timeout or connection error.
    """
    if str(config.USE_OLLAMA) != "1":
        return None
    raw = _safe_str(item.get("summary") or item.get("title"))
    if not raw:
        return None
    import urllib.request
    import json
    prompt = f"Summarize this tech opportunity into 2-3 concise sentences for WhatsApp (no hype, no emojis, factual):\n\n{raw[:1000]}"
    payload = json.dumps({
        "model": "mistral",
        "prompt": prompt,
        "stream": False
    }).encode("utf-8")
    try:
        req = urllib.request.Request(
            "http://localhost:11434/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            res = data.get("response", "").strip()
            return clean_text(res, 450) if res else None
    except Exception:
        return None


def format_whatsapp_draft(item: Dict[str, Any]) -> str:
    """
    Rich WhatsApp Format v3.0 (ASD-STE100):
    8-to-12 line structured template targeting 400-700 characters.
    Omits any line with empty/missing values.
    Supports Hackathons (Template A), Jobs (Template B),
    Workshops/Events (Template C), and Courses/News (Template D).
    """
    if not isinstance(item, dict):
        item = {}

    category = _safe_str(item.get("category") or item.get("type"), "news").lower()
    title = _safe_str(item.get("title"), "Opportunity")
    url = _safe_str(item.get("apply_url") or item.get("url") or item.get("link"))
    badge = get_location_badge(item)
    badge_str = f" {badge}" if badge and badge not in title else ""

    # Summary: Ollama if enabled, otherwise enricher.summarize / clean_text
    summary = ""
    ollama_sum = summarize_with_ollama(item)
    if ollama_sum:
        summary = ollama_sum
    else:
        raw_summary = _safe_str(item.get("summary") or item.get("title"))
        if raw_summary:
            import enricher
            summary = enricher.summarize(raw_summary, max_len=config.SUMMARY_MAX)

    # Common / Enriched attributes
    eligibility = _safe_str(item.get("eligibility"))
    skill_tags = _safe_str(item.get("skill_tags") or item.get("skills"))
    compensation = _safe_str(item.get("compensation") or item.get("stipend") or item.get("salary"))
    perks = _safe_str(item.get("perks"))
    prizes_perks = compensation or perks or _safe_str(item.get("prize"))
    date_val = _safe_str(item.get("deadline") or item.get("event_start") or item.get("date_str"))
    raw_venue = _safe_str(item.get("venue"))
    if not raw_venue and not is_online_accessible(item) and item.get("badge") != "ONLINE":
        raw_venue = format_location_string(item)
    if raw_venue and (raw_venue.lower() in ("online", "virtual", "[online] online", "[online]") or "online" in raw_venue.lower()):
        raw_venue = ""
    venue = raw_venue

    lines = []

    if category in ("hackathon", "hackathons"):
        # Template A: Hackathons
        lines.append(f"⚡ *{title}*{badge_str}")
        lines.append("")
        if summary:
            lines.append("📝 *Overview:*")
            lines.append(summary)
            lines.append("")
        if eligibility:
            lines.append(f"🎯 *Eligibility:* {eligibility}")
        if skill_tags:
            lines.append(f"🛠️ *Tracks / Tech:* {skill_tags}")
        if prizes_perks:
            lines.append(f"🏆 *Prizes & Perks:* {prizes_perks}")
        if date_val:
            lines.append(f"📅 *Date / Deadline:* {date_val}")
        if venue:
            lines.append(f"📍 *Venue:* {venue}")
        if url:
            lines.append("")
            lines.append(f"🔗 *Register Here:* {url}")

    elif category in ("job", "jobs"):
        # Template B: Jobs
        lines.append(f"💼 *{title}*{badge_str}")
        lines.append("")
        if summary:
            lines.append("🏢 *Role Overview:*")
            lines.append(summary)
            lines.append("")
        exp_lvl = _safe_str(item.get("experience_level"))
        if exp_lvl == "FRESHER_INTERN":
            exp_display = "Freshers & Interns (0-1 yrs)"
        elif exp_lvl == "EARLY_MID":
            exp_display = "Early to Mid Career (1-3 yrs)"
        else:
            exp_display = _safe_str(item.get("experience"), "Open to All")

        elig_str = f"{eligibility} | {exp_display}" if eligibility and eligibility != exp_display else (eligibility or exp_display)
        if elig_str:
            lines.append(f"🎓 *Eligibility / Exp:* {elig_str}")
        if skill_tags:
            lines.append(f"🛠️ *Tech Stack:* {skill_tags}")
        if compensation:
            lines.append(f"💰 *CTC / Stipend:* {compensation}")
        
        loc_date_parts = []
        if venue:
            loc_date_parts.append(f"📍 *Location:* {venue}")
        elif not is_online_accessible(item) and item.get("location"):
            loc_date_parts.append(f"📍 *Location:* {item.get('location')}")
        if date_val:
            loc_date_parts.append(f"📅 *Posted / Apply By:* {date_val}")
        if loc_date_parts:
            lines.append(" | ".join(loc_date_parts))

        if url:
            lines.append("")
            lines.append(f"🔗 *Apply Direct:* {url}")

    elif category in ("workshop", "workshops", "event", "events"):
        # Template C: Workshops & Events
        icon = "🛠️" if category in ("workshop", "workshops") else "🗓️"
        lines.append(f"{icon} *{title}*{badge_str}")
        lines.append("")
        if summary:
            lines.append("📌 *What You Will Learn / Expect:*")
            lines.append(summary)
            lines.append("")
        if eligibility:
            lines.append(f"👥 *Who Should Attend:* {eligibility}")
        if skill_tags:
            lines.append(f"🛠️ *Topics Covered:* {skill_tags}")
        if perks:
            lines.append(f"🎁 *Highlights:* {perks}")
        elif _safe_str(item.get("fee")):
            lines.append(f"🎟️ *Fee:* {_safe_str(item.get('fee'))}")
        if date_val:
            lines.append(f"📅 *Date & Time:* {date_val}")
        if venue:
            lines.append(f"📍 *Venue:* {venue}")
        if url:
            lines.append("")
            lines.append(f"🔗 *RSVP Link:* {url}")

    else:
        # Template D: Courses & News
        icon = "🎓" if category in ("course", "courses") else "💡"
        lines.append(f"{icon} *{title}*{badge_str}")
        lines.append("")
        if summary:
            lines.append("📖 *Key Takeaways:*")
            lines.append(summary)
            lines.append("")
        if skill_tags:
            lines.append(f"🛠️ *Skills / Tags:* {skill_tags}")
        access_info = perks or _safe_str(item.get("fee")) or "Free Access"
        lines.append(f"🎁 *Access:* {access_info}")
        if url:
            lines.append("")
            lines.append(f"🔗 *Access Link:* {url}")

    # Remove consecutive empty lines and trailing/leading whitespace
    cleaned_lines = []
    prev_blank = False
    for line in lines:
        is_blank = not line.strip()
        if is_blank and prev_blank:
            continue
        cleaned_lines.append(line)
        prev_blank = is_blank

    res = "\n".join(cleaned_lines).strip()
    return res


def format_whatsapp(item: Dict[str, Any]) -> str:
    """Alias for format_whatsapp_draft for backward compatibility."""
    return format_whatsapp_draft(item)

def get_whatsapp_target_group(category: str) -> str:
    """Returns the matching WhatsApp group name."""
    cat_key = _safe_str(category, "general").lower()
    return CATEGORY_GROUP_MAP.get(cat_key, "General Updates")
