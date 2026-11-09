"""quality_gate.py - run every raw item through gate() BEFORE formatter.py.

gate(item) returns (decision, reason, item)
  decision: "accept" | "review" | "reject"
  item keys used: title, summary, link, source, type
Only "accept" goes to the draft queue. "review" goes to a manual list.
"""
import re
from datetime import datetime
from urllib.parse import urlparse

TECH = re.compile(
    r"\b(ai|ml|llm|agents?|agentic|python|java|javascript|typescript|react|rust|"
    r"cloud|aws|gcp|azure|devops|data|software|developers?|engineers?|engineering|"
    r"coding|hackathon|cyber|security|blockchain|web3|api|kubernetes|docker|"
    r"salesforce|\.net|qa|testing|startup|open source|programming|machine learning|"
    r"intern(ship)?|hiring|workshop|bootcamp|course|certification)\b", re.I)

JUNK = re.compile(
    r"\b(shopping|festival|rugby|sevens|7s|raffles?|concerts?|sale|discounts?|"
    r"cricket|football|fashion|wedding|real estate|casino|betting|horoscope|"
    r"recipes?|holiday|tourists?|vat|jewellery|gold)\b", re.I)

ARTICLE = re.compile(
    r"\b(complete guide|full guide|ultimate guide|breakdown|what you need to know|"
    r"dates, tickets|structured format|day-by-day)\b", re.I)

ONLINE = re.compile(r"\b(online|virtual|webinar|remote|zoom|google meet)\b", re.I)
HYD = re.compile(r"\b(hyderabad|hitec|gachibowli|t-hub|t-works|iiit|kondapur|"
                 r"nanakramguda|madhapur|hicc|hitex|secunderabad)\b", re.I)
OTHER_CITIES = re.compile(
    r"\b(bangalore|bengaluru|delhi|mumbai|pune|chennai|gurgaon|gurugram|noida|"
    r"kolkata|ahmedabad|kochi|jaipur|london|new york|nyc|san francisco|dubai|"
    r"singapore|berlin|paris|toronto|sydney|melbourne|tokyo|boston|los angeles)\b", re.I)

TRUSTED = {
    "devpost.com", "kaggle.com", "hackculture.io", "unstop.com", "luma.com", "lu.ma",
    "commudle.com", "meetup.com", "datacamp.com", "coursera.org", "freecodecamp.org",
    "news.ycombinator.com", "github.com", "linkedin.com", "lnkd.in", "docs.google.com",
    "share.google", "remoteok.com", "weworkremotely.com", "dev.to", "arxiv.org",
    "techcrunch.com", "lobste.rs", "stackoverflow.blog", "machinelearningmastery.com",
    "analyticsvidhya.com", "reddit.com", "community.dev", "gdg.community.dev",
    "github.blog", "example.com", "devfolio.co", "fossunited.org", "greenhouse.io", "lever.co",
}
TRUSTED_DOMAINS = TRUSTED

MAX_SUMMARY_CHARS = 450
MAX_SUMMARY = 450

WORKSHOP_REGEX = re.compile(
    r"\b(workshop|bootcamp|hands-on|live coding|masterclass|study jam|lab|webinar|training session)\b",
    re.I
)

SENIOR_REJECT_REGEX = re.compile(
    r"\b(senior|sr\.|staff|principal|lead|architect|manager|director|vp|head of|[5-9]\+\s*years|1[0-9]\+\s*years)\b",
    re.I
)

EARLY_CAREER_REGEX = re.compile(
    r"\b(intern|internship|fresher|sde-1|sde 1|sde i|graduate|trainee|apprentice|entry level|0-2 years|0-1 years|2025|2026|2027|2028)\b",
    re.I
)


def _domain(url):
    host = urlparse(url or "").netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _is_trusted(url):
    host = _domain(url)
    if not host:
        return False
    if host in TRUSTED:
        return True
    return any(host.endswith("." + t) for t in TRUSTED)


def _trim(text):
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= MAX_SUMMARY_CHARS:
        return text
    cut = text[:MAX_SUMMARY_CHARS]
    # Sentence-boundary truncation before 450 characters
    last_boundary = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    if last_boundary > 80:
        return cut[:last_boundary + 1].strip()
    return cut.rsplit(" ", 1)[0] + "…"


def locate(text, city=None):
    """Return (badge, reason). badge: ONLINE | HYDERABAD - OFFLINE | BENGALURU - OFFLINE | None."""
    if ONLINE.search(text):
        return "ONLINE", ""
    if city == "bengaluru":
        return "BENGALURU - OFFLINE", ""
    if city == "hyderabad":
        return "HYDERABAD - OFFLINE", ""
    if HYD.search(text):
        return "HYDERABAD - OFFLINE", ""
    m = OTHER_CITIES.search(text)
    if m:
        return None, f"offline in other city: {m.group(0)}"
    return None, "location not stated"


def gate(item):
    title = item.get("title", "")
    raw = item.get("summary", "")
    loc = item.get("location") or item.get("where") or ""
    city = item.get("city")
    category = (item.get("category") or item.get("type") or "news").lower()
    head = f"{title} {loc} {raw[:400]}"

    # Check for senior job rejection
    if category in ("job", "jobs"):
        if SENIOR_REJECT_REGEX.search(title):
            return "reject", "senior_role_filtered", item

    if JUNK.search(title) or len(JUNK.findall(head)) >= 2:
        return "reject", "off-topic (not tech)", item
    if not TECH.search(head):
        return "reject", "no tech keyword", item
    if ARTICLE.search(title) or len(raw) > 1500:
        return "reject", "article or listicle, not an opportunity", item

    # Category routing: split events vs workshops
    resolved_category = category
    if category in ("event", "events", "workshop", "workshops"):
        if WORKSHOP_REGEX.search(f"{title} {raw}"):
            resolved_category = "workshops"
        else:
            resolved_category = "events"

    # Experience level classification for jobs
    experience_level = item.get("experience_level", "ALL")
    if category in ("job", "jobs"):
        if EARLY_CAREER_REGEX.search(f"{title} {raw}"):
            experience_level = "FRESHER_INTERN"
        else:
            experience_level = "EARLY_MID"

    badge, why = locate(head, city=city)
    if badge is None and why.startswith("offline"):
        return "reject", why, item

    url = item.get("link") or item.get("url") or ""
    clean = dict(
        item,
        summary=_trim(raw),
        badge=badge,
        link=url,
        url=url,
        category=resolved_category,
        type=resolved_category,
        experience_level=experience_level
    )

    # Compute relevance_score (0 to 100)
    score = 0
    if badge == "HYDERABAD - OFFLINE":
        score += 30
    if experience_level == "FRESHER_INTERN" or clean.get("compensation"):
        score += 25
    
    date_val = clean.get("deadline") or clean.get("event_start") or clean.get("date_str")
    if date_val:
        try:
            import db
            dt = db.parse_date_ist(date_val)
            if dt:
                import config
                diff = (dt.date() - config.now_ist().date()).days
                if 0 <= diff <= 7:
                    score += 20
        except Exception:
            pass
            
    if _is_trusted(url):
        score += 15
    if clean.get("skill_tags"):
        score += 10
    clean["relevance_score"] = min(100, score)

    if badge is None:
        return "review", why, clean
    if not _is_trusted(url):
        return "review", f"untrusted domain: {_domain(url) or 'no link'}", clean
    return "accept", "ok", clean


if __name__ == "__main__":
    samples = [
        {"title": "Rust Events Nov-Dec 2026: Structured Format Needed for Easier Analysis",
         "summary": "Introduction " * 300, "link": "https://dev.to/x", "source": "Dev Events & Workshops"},
        {"title": "Emirates Dubai 7s 2026: Dates, Tickets, Lineup and the Weekend",
         "summary": "Rugby sevens tournament with concerts and tickets.", "link": "https://dev.to/y"},
        {"title": "Dubai Shopping Festival 2026-27: 45 Days of Sales, Raffles",
         "summary": "Discounts and raffles.", "link": "https://dev.to/z"},
        {"title": "HackGlobal by Polaris School of Technology",
         "summary": "A global online hackathon with builders from five countries.",
         "link": "https://hackculture.io"},
        {"title": "Walk-in drive at Ksquare Group: Salesforce and Data Engineering",
         "summary": "Five roles. Venue: Nanakramguda, Hyderabad.", "link": "https://linkedin.com/x"},
        {"title": "AI Meetup in Bangalore", "summary": "In-person at Indiranagar, Bangalore.",
         "link": "https://meetup.com/x"},
    ]
    for s in samples:
        d, r, _ = gate(s)
        print(f"{d:<7} {r:<42} {s['title'][:55]}")
