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
    "github.blog", "example.com",
}

MAX_SUMMARY = 220


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
    if len(text) <= MAX_SUMMARY:
        return text
    cut = text[:MAX_SUMMARY]
    end = max(cut.rfind(". "), cut.rfind("; "))
    return (cut[:end + 1] if end > 80 else cut.rsplit(" ", 1)[0] + "…")


def locate(text):
    """Return (badge, reason). badge: ONLINE | HYDERABAD - OFFLINE | None."""
    if ONLINE.search(text):
        return "ONLINE", ""
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
    head = f"{title} {loc} {raw[:400]}"

    if JUNK.search(title) or len(JUNK.findall(head)) >= 2:
        return "reject", "off-topic (not tech)", item
    if not TECH.search(head):
        return "reject", "no tech keyword", item
    if ARTICLE.search(title) or len(raw) > 1500:
        return "reject", "article or listicle, not an opportunity", item

    badge, why = locate(head)
    if badge is None and why.startswith("offline"):
        return "reject", why, item

    url = item.get("link") or item.get("url") or ""
    clean = dict(item, summary=_trim(raw), badge=badge, link=url, url=url)
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
