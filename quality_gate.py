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
    r"coding|hackathon|cyber|security|blockchain|web3|api|kubernetes|docker|saas|genai|"
    r"salesforce|\.net|qa|testing|startup|product management|open source|programming|machine learning|"
    r"intern(ship)?|hiring|workshop|bootcamp|course|certification)\b", re.I)

JUNK = re.compile(
    r"\b(shopping|festival|rugby|sevens|7s|raffles?|concerts?|sale|discounts?|"
    r"cricket|football|fashion|wedding|real estate|casino|betting|horoscope|"
    r"recipes?|holiday|tourists?|vat|jewellery|gold|scholarship test|demo class|"
    r"toastmasters|industrial pumps|street photography|sign india|grand worship|"
    r"education expo|digital marketing|content creator|mid-career transition|"
    r"summer camp)\b", re.I)

ARTICLE = re.compile(
    r"\b(complete guide|full guide|ultimate guide|breakdown|what you need to know|"
    r"dates, tickets|structured format|day-by-day)\b", re.I)

ONLINE = re.compile(r"\b(online|virtual|webinar|remote|zoom|google meet|work from home|wfh|pan[- ]india|anywhere)\b", re.I)
HYD = re.compile(r"\b(hyderabad|hitec|hi-tech|gachibowli|raidurg|t-hub|thub|t-works|iiit|iit hyderabad|kondapur|"
                 r"nanakramguda|madhapur|hicc|hitex|secunderabad|cokarma|woxsen|mlrit|cmrcet)\b", re.I)
OTHER_CITIES = re.compile(
    r"\b(bangalore|bengaluru|delhi|mumbai|pune|chennai|gurgaon|gurugram|noida|"
    r"kolkata|ahmedabad|kochi|ernakulam|kottayam|kerala|lucknow|jaipur|chandigarh|indore|"
    r"bhopal|nagpur|coimbatore|visakhapatnam|vijayawada|thiruvananthapuram|trivandrum|"
    r"surat|patna|bhubaneswar|chimbote|peru|london|new york|nyc|san francisco|dubai|"
    r"singapore|berlin|paris|toronto|sydney|melbourne|tokyo|boston|los angeles|austin|"
    r"seattle|chicago|amsterdam|munich|zurich)\b", re.I)

DISCUSSION_NOISE_REGEX = re.compile(
    r"(^help!*\b|\bconfused whether\b|\bwhich laptop\b|\babout to buy a laptop\b|\brants?\b|"
    r"\bai slop\b|\bdeserves better\b|\bshould i learn\b|\bworth learning\b|\bhow's the job market\b|"
    r"\blooking for advice\b|\bcareer advice\b|^meet\s+[a-z]+\s+[a-z]+\s*:|^reasons to learn\b|"
    r"\bwhy enterprise llms fail\b|\bstory time\b)",
    re.I,
)

NON_TECH_ROLE_REGEX = re.compile(
    r"\b(account executive|sales representative|\bsdr\b|\bbdr\b|business development|"
    r"collections|accounts payable|renewals manager|telecaller|recruiter|talent acquisition|"
    r"human resources|\bhr\b|registered nurse|accountant|legal counsel|customer support|"
    r"consumer support|content reviewer|video annotation|digital marketing|marketing team|"
    r"volunteering|\bcro\b|resume builder)\b",
    re.I,
)

GEO_RESTRICTED_REMOTE_REGEX = re.compile(
    r"(?:\b(us only|united states only|u\.s\. only|canada only|uk only|eu only|"
    r"europe only|\bdach\b|\bemea\b|\blatam\b|boston, ma|san francisco, ca|new york, ny)\b|"
    r"\bremote,?\s*(?:united states|\busa?\b|\bu\.s\.?\b|canada|uk|united kingdom|germany|france|spain|bulgaria|poland|brazil|mexico|argentina|colombia|australia|ireland|netherlands)\b|"
    r"\bremote eligible in (?!india|apac|asia)[a-z]+)",
    re.I,
)

TRUSTED = {
    "devpost.com", "kaggle.com", "hackculture.io", "unstop.com", "luma.com", "lu.ma",
    "commudle.com", "meetup.com", "datacamp.com", "coursera.org", "freecodecamp.org",
    "news.ycombinator.com", "github.com", "linkedin.com", "lnkd.in", "docs.google.com",
    "share.google", "remoteok.com", "weworkremotely.com", "dev.to", "arxiv.org",
    "techcrunch.com", "lobste.rs", "stackoverflow.blog", "machinelearningmastery.com",
    "analyticsvidhya.com", "reddit.com", "community.dev", "gdg.community.dev",
    "github.blog", "example.com", "devfolio.co", "fossunited.org", "greenhouse.io", "lever.co",
    "deeplearning.ai", "huggingface.co", "aws.amazon.com", "wellfound.com", "naukri.com", "internshala.com",
    "allevents.in", "aitinkerers.org", "konfhub.com", "townscript.com",
}
TRUSTED_DOMAINS = TRUSTED

MAX_SUMMARY_CHARS = 450
MAX_SUMMARY = 450

WORKSHOP_REGEX = re.compile(
    r"\b(workshop|bootcamp|hands-on|live coding|masterclass|study jam|lab|webinar|training session)\b",
    re.I
)

SENIOR_REJECT_REGEX = re.compile(
    r"(?:\b(?:senior|staff|principal|lead|architect|manager|director|vp|head of|[5-9]\+\s*years|1[0-9]\+\s*years)\b|\bsr\.?(?=\s|$|[,:-]))",
    re.I
)

EARLY_CAREER_REGEX = re.compile(
    r"\b(intern|internship|fresher|sde-1|sde 1|sde i|graduate|trainee|apprentice|entry level|0-2 years|0-1 years|2025|2026|2027|2028)\b",
    re.I
)


def _domain(url):
    host = urlparse(url or "").netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _is_trusted(url, source=""):
    host = _domain(url)
    if not host:
        return False
    # Google News RSS job links from our vetted Search Jobs RSS collector pass if tech + non-senior + geo checks already passed
    if host == "news.google.com" and str(source) == "Search Jobs RSS":
        return True
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


def locate(text, city=None, explicit_text=""):
    """Return (badge, reason). badge: ONLINE | HYDERABAD - OFFLINE | BENGALURU - OFFLINE | None."""
    content = explicit_text or text
    # Explicit online/remote keyword in title or summary takes precedence over chapter defaults
    if ONLINE.search(content):
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
    if ONLINE.search(text):
        return "ONLINE", ""
    return None, "location not stated"


def compute_relevance_score(item: dict) -> int:
    """Computes relevance score (0-100) cleanly for both pre- and post-enrichment items."""
    score = 0
    badge = item.get("badge") or ""
    if badge == "HYDERABAD - OFFLINE":
        score += 30
    elif badge == "BENGALURU - OFFLINE":
        score += 25
    if item.get("experience_level") == "FRESHER_INTERN" or item.get("compensation") or item.get("stipend"):
        score += 25

    date_val = item.get("deadline") or item.get("event_start") or item.get("date_str")
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

    url = item.get("url") or item.get("link") or ""
    if _is_trusted(url, source=item.get("source", "")):
        score += 15
    if item.get("skill_tags") or item.get("skills"):
        score += 10
    return min(100, score)


def gate(item):
    title = item.get("title", "")
    raw = item.get("summary", "")
    loc = item.get("location") or item.get("where") or ""
    city = item.get("city")
    source = str(item.get("source") or "")
    category = (item.get("category") or item.get("type") or "news").lower()
    head = f"{title} {loc} {raw[:400]}"
    content_only = f"{title} {raw[:400]}"

    if re.match(r"^https?://", title.strip()):
        return "reject", "raw URL as title", item

    if re.search(r"\bcommunity calendar\b", title, re.I) or str(item.get("deadline") or "").strip().lower() == "check calendar":
        return "reject", "calendar hub link, not an event", item

    # Guard Indian city chapters against leaked foreign-timezone events (e.g. -05:00 Peru DevFests)
    date_ctx = str(item.get("event_start") or item.get("date_str") or "").strip()
    if (city in ("hyderabad", "bengaluru") or "gdg" in source.lower()) and date_ctx:
        if re.search(r"[+-](?!05:30)\d{2}:\d{2}$", date_ctx) and not HYD.search(title) and not any(
            k in title.lower() for k in ("hyderabad", "bengaluru", "bangalore")
        ):
            return "reject", "offline in other city: foreign timezone event", item

    # Check for senior & non-tech/geo-restricted job rejection
    if category in ("job", "jobs"):
        if SENIOR_REJECT_REGEX.search(title):
            return "reject", "senior_role_filtered", item
        if NON_TECH_ROLE_REGEX.search(title):
            return "reject", "non_tech_role_filtered", item
        if GEO_RESTRICTED_REMOTE_REGEX.search(head) and not HYD.search(head) and city not in ("hyderabad", "bengaluru"):
            return "reject", "offline in other city: geo-restricted remote", item

    if JUNK.search(title) or len(JUNK.findall(head)) >= 2:
        return "reject", "off-topic (not tech)", item
    if DISCUSSION_NOISE_REGEX.search(title) or DISCUSSION_NOISE_REGEX.search(raw[:200]):
        return "reject", "discussion or opinion noise, not an opportunity", item
    if not TECH.search(head):
        return "reject", "no tech keyword", item
    if ARTICLE.search(title) or len(raw) > 1500:
        return "reject", "article or listicle, not an opportunity", item

    # Category routing: normalize & split events vs workshops
    resolved_category = category
    if category in ("event", "events", "workshop", "workshops"):
        if WORKSHOP_REGEX.search(f"{title} {raw}"):
            resolved_category = "workshops"
        else:
            resolved_category = "events"
    elif category in ("job", "jobs"):
        url_low = str(item.get("url") or item.get("link") or "").lower()
        if "dev.to/" in url_low and not re.search(r"\b(hiring|apply now|job opening|internship at|engineer at|developer at|is hiring)\b", title, re.I):
            resolved_category = "news"
        else:
            resolved_category = "job"
    elif category in ("hackathon", "hackathons"):
        url_low = str(item.get("url") or item.get("link") or "").lower()
        if "dev.to/" in url_low and (
            re.search(r"\b(is over|what the winners|i built|i turned|how we built|kinda hard)\b", title, re.I)
            or not re.search(r"\b(announcing|register|submissions? open|prize pool|join the .* challenge|official challenge)\b", f"{title} {raw[:300]}", re.I)
        ):
            resolved_category = "news"
        else:
            resolved_category = "hackathon"
    elif category in ("course", "courses"):
        url_low = str(item.get("url") or item.get("link") or "").lower()
        if "arxiv.org" in url_low or source.lower().startswith("arxiv"):
            resolved_category = "news"
        elif "reddit.com" in url_low or source.lower().startswith("reddit"):
            resolved_category = "news"
        elif WORKSHOP_REGEX.search(title):
            resolved_category = "workshops"
        else:
            resolved_category = "course"

    # Experience level classification for jobs
    experience_level = item.get("experience_level", "ALL")
    if resolved_category == "job":
        if EARLY_CAREER_REGEX.search(f"{title} {raw}"):
            experience_level = "FRESHER_INTERN"
        else:
            experience_level = "EARLY_MID"

    badge, why = locate(head, city=city, explicit_text=content_only)
    if badge is None and why.startswith("offline"):
        return "reject", why, item

    url = item.get("link") or item.get("url") or ""
    resolved_city = city
    if not resolved_city or (resolved_city == "other" and badge in ("HYDERABAD - OFFLINE", "BENGALURU - OFFLINE", "ONLINE")):
        if badge == "HYDERABAD - OFFLINE":
            resolved_city = "hyderabad"
        elif badge == "BENGALURU - OFFLINE":
            resolved_city = "bengaluru"
        elif badge == "ONLINE":
            resolved_city = "online"

    clean = dict(
        item,
        summary=_trim(raw),
        badge=badge,
        city=resolved_city,
        link=url,
        url=url,
        category=resolved_category,
        type=resolved_category,
        experience_level=experience_level
    )

    clean["relevance_score"] = compute_relevance_score(clean)

    if badge is None:
        return "review", why, clean
    if not _is_trusted(url, source=source):
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
