"""
collectors2.py - Legion Automation System 34 Sources Collector & Health Monitor
Provides resilient fetching, feed parsing (RSS/Atom, iCal, JSON API, Luma),
and health diagnostic table runner.
"""

import argparse
import html
import json
import logging
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import quality_gate

# Ensure UTF-8 stdout on Windows
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

logger = logging.getLogger("legion.collectors2")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s] %(message)s")

# SSL Context with certifi if available
try:
    import certifi
    SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:
    SSL_CTX = ssl.create_default_context()

DEFAULT_TIMEOUT = 10
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
CURL_USER_AGENT = "curl/8.0"

TAG_RE = re.compile(r"<[^>]+>")


def strip_html_tags(text: str) -> str:
    """Removes HTML tags, style/script blocks, comments, and unescapes entities."""
    if not text:
        return ""
    clean = re.sub(r"<(script|style)[^>]*>[\s\S]*?</\1>", " ", text, flags=re.IGNORECASE)
    clean = re.sub(r"<!--[\s\S]*?-->", " ", clean)
    clean = re.sub(r"<(?:p|div|br|li|h[1-6]|tr|td)(?:\s+[^>]*>|>)", " ", clean, flags=re.IGNORECASE)
    clean = TAG_RE.sub("", clean)
    clean = html.unescape(clean)
    return " ".join(clean.split())


def fetch_raw(url: str, headers: Optional[Dict[str, str]] = None, timeout: int = DEFAULT_TIMEOUT) -> Tuple[Optional[bytes], Optional[str]]:
    """
    Fetches raw bytes from URL.
    Returns (bytes_data, error_message).
    """
    if not url:
        return None, "empty url"
    req_headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": "*/*",
    }
    if headers:
        req_headers.update(headers)

    req = urllib.request.Request(url, headers=req_headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as resp:
            data = resp.read()
            return data, None
    except urllib.error.HTTPError as e:
        return None, f"HTTP Error {e.code}: {e.reason}"
    except urllib.error.URLError as e:
        return None, f"URLError: {e.reason}"
    except TimeoutError:
        return None, f"Timeout after {timeout}s"
    except Exception as e:
        return None, str(e)


# ==============================================================================
# CONFIG: 34 Sources Specification
# ==============================================================================

SOURCES: Dict[str, Dict[str, Any]] = {
    # 1. Hacker News API & RSS
    "hn_api": {
        "name": "Hacker News API",
        "url": "https://hacker-news.firebaseio.com/v0/topstories.json",
        "type": "hn_api",
        "category": "news",
        "headers": {"User-Agent": DEFAULT_USER_AGENT},
    },
    "hn_rss": {
        "name": "Hacker News RSS",
        "url": "https://news.ycombinator.com/rss",
        "type": "xml",
        "category": "news",
        # Hacker News RSS returns 419 to browser UAs; curl UA works reliably
        "headers": {"User-Agent": CURL_USER_AGENT},
    },
    # 2. Dev.to API / RSS (hackathons, career, news)
    "devto_hackathons": {
        "name": "Dev.to Hackathons RSS",
        "url": "https://dev.to/feed/tag/hackathon",
        "type": "xml",
        "category": "hackathon",
        "headers": {"User-Agent": DEFAULT_USER_AGENT},
    },
    "devto_career": {
        "name": "Dev.to Career RSS",
        "url": "https://dev.to/feed/tag/career",
        "type": "xml",
        "category": "job",
        "headers": {"User-Agent": DEFAULT_USER_AGENT},
    },
    "devto_news": {
        "name": "Dev.to News RSS",
        "url": "https://dev.to/feed/tag/news",
        "type": "xml",
        "category": "news",
        "headers": {"User-Agent": DEFAULT_USER_AGENT},
    },
    "devto_api": {
        "name": "Dev.to API Hackathons",
        "url": "https://dev.to/api/articles?tag=hackathon",
        "type": "devto_api",
        "category": "hackathon",
        "headers": {"User-Agent": DEFAULT_USER_AGENT},
    },
    # 3. Devpost API
    "devpost_api": {
        "name": "Devpost API",
        "url": "https://devpost.com/api/hackathons",
        "type": "devpost_api",
        "category": "hackathon",
        "headers": {"User-Agent": CURL_USER_AGENT},
    },
    # 4. RemoteOK API & RSS
    "remoteok_api": {
        "name": "RemoteOK API",
        "url": "https://remoteok.com/api",
        "type": "remoteok_api",
        "category": "job",
        "headers": {"User-Agent": DEFAULT_USER_AGENT},
    },
    "remoteok_rss": {
        "name": "RemoteOK RSS",
        "url": "https://remoteok.com/remote-jobs.rss",
        "type": "xml",
        "category": "job",
        "headers": {"User-Agent": DEFAULT_USER_AGENT},
    },
    # 5. WeWorkRemotely RSS feeds (programming, devops, data/backend, all jobs)
    "wwr_programming": {
        "name": "WeWorkRemotely Programming",
        "url": "https://weworkremotely.com/categories/remote-programming-jobs.rss",
        "type": "xml",
        "category": "job",
        "headers": {"User-Agent": DEFAULT_USER_AGENT},
    },
    "wwr_devops": {
        "name": "WeWorkRemotely DevOps",
        "url": "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss",
        "type": "xml",
        "category": "job",
        "headers": {"User-Agent": DEFAULT_USER_AGENT},
    },
    "wwr_data_backend": {
        "name": "WeWorkRemotely Back-End",
        "url": "https://weworkremotely.com/categories/remote-back-end-programming-jobs.rss",
        "type": "xml",
        "category": "job",
        "headers": {"User-Agent": DEFAULT_USER_AGENT},
    },
    "wwr_all": {
        "name": "WeWorkRemotely All Jobs",
        "url": "https://weworkremotely.com/remote-jobs.rss",
        "type": "xml",
        "category": "job",
        "headers": {"User-Agent": DEFAULT_USER_AGENT},
    },
    # 6. Reddit RSS feeds
    "reddit_ml": {
        "name": "Reddit r/learnmachinelearning",
        "url": "https://www.reddit.com/r/learnmachinelearning/hot.rss",
        "type": "xml",
        "category": "course",
        "headers": {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 LegionCommunityBot/1.0"},
    },
    "reddit_datascience": {
        "name": "Reddit r/datascience",
        "url": "https://www.reddit.com/r/datascience/hot.rss",
        "type": "xml",
        "category": "course",
        "headers": {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 LegionCommunityBot/1.0"},
    },
    "reddit_python": {
        "name": "Reddit r/Python",
        "url": "https://www.reddit.com/r/Python/hot.rss",
        "type": "xml",
        "category": "news",
        "headers": {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 LegionCommunityBot/1.0"},
    },
    "reddit_cscareer": {
        "name": "Reddit r/cscareerquestions",
        "url": "https://www.reddit.com/r/cscareerquestions/hot.rss",
        "type": "xml",
        "category": "job",
        "headers": {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 LegionCommunityBot/1.0"},
    },
    # 7. FreeCodeCamp RSS
    "freecodecamp_rss": {
        "name": "FreeCodeCamp RSS",
        "url": "https://www.freecodecamp.org/news/rss/",
        "type": "xml",
        "category": "course",
        "headers": {"User-Agent": DEFAULT_USER_AGENT},
    },
    # 8. GitHub Trending / Explore feeds
    "github_blog": {
        "name": "GitHub Blog RSS",
        "url": "https://github.blog/feed/",
        "type": "xml",
        "category": "news",
        "headers": {"User-Agent": DEFAULT_USER_AGENT},
    },
    "github_changelog": {
        "name": "GitHub Changelog RSS",
        "url": "https://github.blog/changelog/feed/",
        "type": "xml",
        "category": "news",
        "headers": {"User-Agent": DEFAULT_USER_AGENT},
    },
    # 9. ArXiv CS / AI RSS
    "arxiv_ai": {
        "name": "ArXiv CS.AI RSS",
        "url": "https://rss.arxiv.org/rss/cs.AI",
        "type": "xml",
        "category": "news",
        "headers": {"User-Agent": DEFAULT_USER_AGENT},
    },
    "arxiv_cs": {
        "name": "ArXiv CS.LG RSS",
        "url": "https://rss.arxiv.org/rss/cs.LG",
        "type": "xml",
        "category": "news",
        "headers": {"User-Agent": DEFAULT_USER_AGENT},
    },
    # 10. Hyderabad Tech Communities (GDG, HydPy, React, AWS, AI)
    "gdg_hyderabad": {
        "name": "GDG Hyderabad",
        "url": "https://gdg.community.dev/api/chapter/626/event/",
        "type": "bevy_api",
        "category": "event",
        "headers": {"User-Agent": DEFAULT_USER_AGENT},
    },
    "hydpy": {
        "name": "HydPy",
        "url": "https://www.meetup.com/hyderabad-python-user-group/events/ical/",
        "type": "ical",
        "category": "event",
        "headers": {"User-Agent": DEFAULT_USER_AGENT},
    },
    "react_hyderabad": {
        "name": "React Hyderabad",
        "url": "https://lu.ma/reacthyderabad",
        "type": "luma",
        "category": "event",
        "headers": {"User-Agent": DEFAULT_USER_AGENT},
    },
    "aws_hyderabad": {
        "name": "AWS User Group Hyderabad",
        "url": "https://www.meetup.com/awsughyd/events/ical/",
        "type": "ical",
        "category": "event",
        "headers": {"User-Agent": DEFAULT_USER_AGENT},
    },
    "ai_community_hyderabad": {
        "name": "AI Community Hyderabad",
        "url": "https://www.meetup.com/hyderabad-artificial-intelligence-meetup-group/events/ical/",
        "type": "ical",
        "category": "event",
        "headers": {"User-Agent": DEFAULT_USER_AGENT},
    },
    # 11. TechCrunch RSS
    "techcrunch": {
        "name": "TechCrunch RSS",
        "url": "https://techcrunch.com/feed/",
        "type": "xml",
        "category": "news",
        "headers": {"User-Agent": DEFAULT_USER_AGENT},
    },
    # 12. Lobste.rs RSS
    "lobsters": {
        "name": "Lobste.rs RSS",
        "url": "https://lobste.rs/rss",
        "type": "xml",
        "category": "news",
        "headers": {"User-Agent": DEFAULT_USER_AGENT},
    },
    # 13. PyCon India / Conf feeds
    "pycon_india": {
        "name": "PyCon India Feed",
        "url": "https://in.pycon.org/blog/rss.xml",
        "type": "xml",
        "category": "event",
        "headers": {"User-Agent": DEFAULT_USER_AGENT},
    },
    # 14. Machine Learning Mastery / Analytics Vidhya RSS
    "ml_mastery": {
        "name": "Machine Learning Mastery RSS",
        "url": "https://machinelearningmastery.com/feed/",
        "type": "xml",
        "category": "course",
        "headers": {"User-Agent": CURL_USER_AGENT},
    },
    "analytics_vidhya": {
        "name": "Analytics Vidhya RSS",
        "url": "https://www.analyticsvidhya.com/feed/",
        "type": "xml",
        "category": "course",
        "headers": {"User-Agent": DEFAULT_USER_AGENT},
    },
    # 15. StackOverflow Jobs / Tech feeds
    "stackoverflow_blog": {
        "name": "StackOverflow Blog RSS",
        "url": "https://stackoverflow.blog/feed/",
        "type": "xml",
        "category": "news",
        "headers": {"User-Agent": CURL_USER_AGENT},
    },
    "stackoverflow_tech": {
        "name": "StackOverflow Tech RSS",
        "url": "https://stackoverflow.blog/category/technology/feed/",
        "type": "xml",
        "category": "news",
        "headers": {"User-Agent": CURL_USER_AGENT},
    },
    # 16. Bengaluru & Hyderabad Local Ecosystem Sources
    "gdg_bangalore": {
        "name": "GDG Bangalore",
        "url": "https://gdg.community.dev/api/chapter/182/event/",
        "type": "bevy_api",
        "category": "event",
        "city": "bengaluru",
        "headers": {"User-Agent": DEFAULT_USER_AGENT},
    },
    "bangpypers": {
        "name": "BangPypers",
        "url": "https://www.meetup.com/bangpypers/events/ical/",
        "type": "ical",
        "category": "event",
        "city": "bengaluru",
        "headers": {"User-Agent": DEFAULT_USER_AGENT},
    },
    "hasgeek": {
        "name": "Hasgeek",
        "url": "https://hasgeek.com/feed",
        "type": "xml",
        "category": "event",
        "city": "bengaluru",
        "headers": {"User-Agent": DEFAULT_USER_AGENT},
    },
    "commudle_bengaluru": {
        "name": "Commudle Bengaluru",
        "url": "https://api.commudle.com/api/v1/events?city=Bengaluru",
        "type": "commudle",
        "category": "event",
        "city": "bengaluru",
        "headers": {"User-Agent": DEFAULT_USER_AGENT},
    },
    "luma_bengaluru": {
        "name": "Luma Bengaluru",
        "url": "https://api.lu.ma/discover/events?location=Bengaluru",
        "type": "luma",
        "category": "event",
        "city": "bengaluru",
        "headers": {"User-Agent": DEFAULT_USER_AGENT},
    },
    "commudle_hyderabad": {
        "name": "Commudle Hyderabad",
        "url": "https://api.commudle.com/api/v1/events?city=Hyderabad",
        "type": "commudle",
        "category": "event",
        "city": "hyderabad",
        "headers": {"User-Agent": DEFAULT_USER_AGENT},
    },
    "luma_hyderabad": {
        "name": "Luma Hyderabad",
        "url": "https://api.lu.ma/discover/events?location=Hyderabad",
        "type": "luma",
        "category": "event",
        "city": "hyderabad",
        "headers": {"User-Agent": DEFAULT_USER_AGENT},
    },
}

# Feeds that have failed or were deprecated by upstream providers
REMOVED_SOURCES: Dict[str, str] = {
    "remoteok_rss": "RemoteOK discontinued RSS syndication with HTTP 410 Gone; use RemoteOK API instead.",
    "pycon_india": "PyCon India discontinued blog RSS endpoint with HTTP 404; active updates moved to social/conference site.",
}


# ==============================================================================
# PARSERS
# ==============================================================================

def _find_first(parent: ET.Element, tags: List[str]) -> Optional[ET.Element]:
    for t in tags:
        el = parent.find(t)
        if el is not None:
            return el
    return None


def parse_xml_feed(raw_bytes: bytes, source_name: str, category: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Parses RSS 2.0 or Atom XML bytes into normalized opportunity dicts."""
    items: List[Dict[str, Any]] = []
    if not raw_bytes:
        return items
    try:
        root = ET.fromstring(raw_bytes)
    except ET.ParseError as e:
        logger.warning(f"XML parse error for {source_name}: {e}")
        return items

    atom_ns = "{http://www.w3.org/2005/Atom}"
    atom_entries = root.findall(f".//{atom_ns}entry")
    if not atom_entries:
        atom_entries = root.findall(".//entry")
    if atom_entries:
        for entry in atom_entries[:limit]:
            title_el = _find_first(entry, [f"{atom_ns}title", "title"])
            title = title_el.text.strip() if (title_el is not None and title_el.text) else "Untitled"
            link = ""
            link_candidates = entry.findall(f"{atom_ns}link") or entry.findall("link")
            for lk in link_candidates:
                href = lk.get("href") or lk.text
                if href:
                    link = href.strip()
                    break
            summary_el = _find_first(entry, [f"{atom_ns}summary", f"{atom_ns}content", "summary", "content"])
            summary = strip_html_tags(summary_el.text) if (summary_el is not None and summary_el.text) else ""
            date_el = _find_first(entry, [f"{atom_ns}updated", f"{atom_ns}published", "updated", "published"])
            raw_date = date_el.text.strip() if (date_el is not None and date_el.text) else ""
            clean_date = ""
            if category in ("event", "hackathon"):
                m = re.search(r'\b(20\d{2}-\d{2}-\d{2})\b', raw_date)
                if m:
                    clean_date = m.group(1)

            if title and link:
                items.append({
                    "id": f"{source_name}_{link[-24:]}",
                    "title": title,
                    "summary": summary or f"Update from {source_name}: {title}",
                    "link": link,
                    "url": link,
                    "source": source_name,
                    "category": category,
                    "type": category,
                    "date": clean_date,
                    "date_str": raw_date,
                    "deadline": "See details",
                    "location": "Online",
                    "where": "Online",
                    "posted": datetime.now().strftime("%Y-%m-%d"),
                })
        return items

    # RSS 2.0
    rss_items = root.findall(".//item")
    for it in rss_items[:limit]:
        title_el = it.find("title")
        title = title_el.text.strip() if (title_el is not None and title_el.text) else "Untitled"
        link_el = it.find("link")
        link = link_el.text.strip() if (link_el is not None and link_el.text) else ""
        desc_el = it.find("description")
        summary = strip_html_tags(desc_el.text) if (desc_el is not None and desc_el.text) else ""
        date_el = it.find("pubDate")
        raw_date = date_el.text.strip() if (date_el is not None and date_el.text) else ""
        clean_date = ""
        if category in ("event", "hackathon"):
            m = re.search(r'\b(20\d{2}-\d{2}-\d{2})\b', raw_date)
            if m:
                clean_date = m.group(1)

        if title and link:
            items.append({
                "id": f"{source_name}_{link[-24:]}",
                "title": title,
                "summary": summary or f"Update from {source_name}: {title}",
                "link": link,
                "url": link,
                "source": source_name,
                "category": category,
                "type": category,
                "date": clean_date,
                "date_str": raw_date,
                "deadline": "See details",
                "location": "Online",
                "where": "Online",
                "posted": datetime.now().strftime("%Y-%m-%d"),
            })
    return items


def parse_ical(raw_text: str, source_name: str, limit: int = 10, city: str = "hyderabad") -> List[Dict[str, Any]]:
    """Parses iCal VEVENT blocks into normalized event dicts."""
    items: List[Dict[str, Any]] = []
    if not raw_text:
        return items
    # Unfold lines (CRLF + space or tab)
    unfolded = re.sub(r'\r?\n[ \t]', '', raw_text)
    events = re.findall(r'BEGIN:VEVENT([\s\S]*?)END:VEVENT', unfolded)
    for block in events[:limit]:
        fields: Dict[str, str] = {}
        for line in block.strip().splitlines():
            if ":" not in line:
                continue
            k_part, val = line.split(":", 1)
            key = k_part.split(";")[0].upper().strip()
            val = val.strip().replace(r'\,', ',').replace(r'\;', ';').replace(r'\n', ' ')
            fields[key] = val

        title = fields.get("SUMMARY", "Community Event")
        link = fields.get("URL", "")
        summary = fields.get("DESCRIPTION", "")
        dtstart = fields.get("DTSTART", "")
        clean_date = ""
        if dtstart:
            m = re.search(r'(\d{4})(\d{2})(\d{2})', dtstart)
            if m:
                clean_date = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

        location = fields.get("LOCATION") or (city.capitalize() if city else "Online")
        items.append({
            "id": f"{source_name}_{clean_date}_{title[:16]}",
            "title": title,
            "summary": strip_html_tags(summary)[:220] or f"Meetup hosted by {source_name}: {title}",
            "link": link or "https://meetup.com",
            "url": link or "https://meetup.com",
            "source": source_name,
            "category": "event",
            "type": "event",
            "date": clean_date,
            "date_str": clean_date,
            "deadline": "RSVP on Meetup",
            "location": location,
            "where": location,
            "posted": datetime.now().strftime("%Y-%m-%d"),
        })
    return items


def parse_devpost_api(data: bytes, limit: int = 10) -> List[Dict[str, Any]]:
    """Parses Devpost API /api/hackathons response."""
    items: List[Dict[str, Any]] = []
    try:
        j = json.loads(data.decode("utf-8", errors="ignore"))
        hackathons = j.get("hackathons", [])
        for h in hackathons[:limit]:
            title = h.get("title", "Hackathon")
            url = h.get("url", "https://devpost.com")
            themes_list = [t.get("name") for t in h.get("themes", []) if t.get("name")]
            themes_str = f"Themes: {', '.join(themes_list)}. " if themes_list else ""
            loc_str = h.get("displayed_location", {}).get("location") or "Online"
            summary = strip_html_tags(f"Online developer hackathon on Devpost. {themes_str}{loc_str}.")[:220]
            dates = h.get("submission_period_dates", "")
            clean_date = ""
            m = re.search(r'\b(20\d{2}-\d{2}-\d{2})\b', dates)
            if m:
                clean_date = m.group(1)
            else:
                m_range = re.search(r'([A-Za-z]{3})\s+(\d{1,2}),?\s+(20\d{2})', dates)
                if m_range:
                    try:
                        dt = datetime.strptime(f"{m_range.group(1)} {m_range.group(2)} {m_range.group(3)}", "%b %d %Y")
                        clean_date = dt.strftime("%Y-%m-%d")
                    except Exception:
                        pass

            items.append({
                "id": f"devpost_{h.get('id', title[:16])}",
                "title": title,
                "summary": summary,
                "link": url,
                "url": url,
                "source": "Devpost",
                "category": "hackathon",
                "type": "hackathon",
                "date": clean_date,
                "date_str": dates,
                "deadline": dates or "See Devpost",
                "location": "Online",
                "where": "Online",
                "posted": datetime.now().strftime("%Y-%m-%d"),
            })
    except Exception as e:
        logger.warning(f"Error parsing Devpost API: {e}")
    return items


def parse_remoteok_api(data: bytes, limit: int = 10) -> List[Dict[str, Any]]:
    """Parses RemoteOK API JSON response."""
    items: List[Dict[str, Any]] = []
    try:
        j = json.loads(data.decode("utf-8", errors="ignore"))
        if isinstance(j, list):
            for job in j:
                if len(items) >= limit:
                    break
                if not isinstance(job, dict) or "position" not in job:
                    continue
                role = str(job.get("position") or "Software Engineer").strip()
                company = str(job.get("company") or "Tech Company").strip()
                url = str(job.get("url") or "").strip()
                if url.startswith("/"):
                    url = "https://remoteok.com" + url
                desc = strip_html_tags(str(job.get("description") or ""))[:220]
                loc = str(job.get("location") or "Remote")
                tags = job.get("tags") or []
                skills = ", ".join(tags[:4]) if isinstance(tags, list) else "Tech"

                items.append({
                    "id": f"remoteok_{job.get('id', role[:16])}",
                    "title": f"{role} at {company}",
                    "role": role,
                    "company": company,
                    "summary": desc or f"Remote role: {role} at {company}.",
                    "link": url,
                    "url": url,
                    "source": "RemoteOK",
                    "category": "job",
                    "type": "job",
                    "skills": skills,
                    "date": "",
                    "date_str": str(job.get("date") or "")[:10],
                    "deadline": "Until filled",
                    "location": loc,
                    "where": loc,
                    "posted": datetime.now().strftime("%Y-%m-%d"),
                })
    except Exception as e:
        logger.warning(f"Error parsing RemoteOK API: {e}")
    return items


def parse_bevy_api(data: bytes, source_name: str, limit: int = 10, city: str = "hyderabad") -> List[Dict[str, Any]]:
    """Parses Bevy chapter API (e.g. GDG Hyderabad, GDG Bangalore) JSON."""
    items: List[Dict[str, Any]] = []
    try:
        j = json.loads(data.decode("utf-8", errors="ignore"))
        results = j.get("results", [])
        for ev in results[:limit]:
            title = ev.get("title", "Community Event")
            raw_url = ev.get("url") or ""
            url = raw_url if raw_url.startswith("http") else f"https://gdg.community.dev{raw_url}"
            summary = strip_html_tags(ev.get("description_short") or ev.get("description") or "")[:220]
            start_date = str(ev.get("start_date") or "")
            clean_date = start_date[:10] if len(start_date) >= 10 and "-" in start_date[:10] else ""

            loc_val = city.capitalize() if city else "Online"
            items.append({
                "id": f"bevy_{ev.get('id', title[:16])}",
                "title": title,
                "summary": summary or f"Developer meetup organized by {source_name}: {title}.",
                "link": url,
                "url": url,
                "source": source_name,
                "category": "event",
                "type": "event",
                "city": city,
                "date": clean_date,
                "date_str": start_date,
                "deadline": "RSVP on Chapter Page",
                "location": loc_val,
                "where": loc_val,
                "posted": config.now_ist().strftime("%Y-%m-%d"),
            })
    except Exception as e:
        logger.warning(f"Error parsing Bevy API: {e}")
    return items


def parse_luma(html_text: str, source_name: str, limit: int = 10, city: str = "hyderabad") -> List[Dict[str, Any]]:
    """Parses Luma calendar page HTML by extracting Next.js __NEXT_DATA__."""
    items: List[Dict[str, Any]] = []
    try:
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html_text)
        if m:
            data = json.loads(m.group(1))
            inner = data.get("props", {}).get("pageProps", {}).get("initialData", {}).get("data", {})
            events = inner.get("upcoming", {}).get("entries", []) or inner.get("featured_items", [])
            for it in events[:limit]:
                ev = it.get("event", {})
                title = ev.get("name", "Tech Meetup")
                slug = ev.get("url", "")
                url = f"https://lu.ma/{slug}" if slug else "https://lu.ma"
                summary = strip_html_tags(ev.get("description", ""))[:220]
                start_at = str(ev.get("start_at", ""))
                clean_date = start_at[:10] if len(start_at) >= 10 and "-" in start_at[:10] else ""

                loc_val = city.capitalize() if city else "Online"
                items.append({
                    "id": f"luma_{ev.get('api_id', title[:16])}",
                    "title": title,
                    "summary": summary or f"Event hosted by {source_name}: {title}.",
                    "link": url,
                    "url": url,
                    "source": source_name,
                    "category": "event",
                    "type": "event",
                    "city": city,
                    "date": clean_date,
                    "date_str": start_at,
                    "deadline": "RSVP on Luma",
                    "location": loc_val,
                    "where": loc_val,
                    "posted": config.now_ist().strftime("%Y-%m-%d"),
                })
    except Exception as e:
        logger.warning(f"Error parsing Luma page: {e}")
    return items


def parse_hn_api(data: bytes, limit: int = 5) -> List[Dict[str, Any]]:
    """Parses Hacker News official Firebase REST API."""
    items: List[Dict[str, Any]] = []
    try:
        story_ids = json.loads(data.decode("utf-8"))
        if isinstance(story_ids, list):
            for sid in story_ids[:limit]:
                item_url = f"https://hacker-news.firebaseio.com/v0/item/{sid}.json"
                raw, _ = fetch_raw(item_url, timeout=5)
                if not raw:
                    continue
                story = json.loads(raw.decode("utf-8"))
                if not isinstance(story, dict) or story.get("type") != "story":
                    continue
                title = str(story.get("title", "")).strip()
                link = str(story.get("url") or f"https://news.ycombinator.com/item?id={sid}").strip()
                score = story.get("score", 0)
                by = story.get("by", "community")
                summary = f"Trending on Hacker News ({score} points) posted by @{by}."

                items.append({
                    "id": f"hn_{sid}",
                    "title": title,
                    "summary": summary,
                    "link": link,
                    "url": link,
                    "source": "Hacker News API",
                    "category": "news",
                    "type": "news",
                    "date": "",
                    "date_str": datetime.now().strftime("%Y-%m-%d"),
                    "deadline": "Published",
                    "location": "Online",
                    "where": "Online",
                    "posted": datetime.now().strftime("%Y-%m-%d"),
                })
    except Exception as e:
        logger.warning(f"Error parsing HN API: {e}")
    return items


def parse_devto_api(data: bytes, limit: int = 10) -> List[Dict[str, Any]]:
    """Parses Dev.to articles API."""
    items: List[Dict[str, Any]] = []
    try:
        articles = json.loads(data.decode("utf-8", errors="ignore"))
        if isinstance(articles, list):
            for art in articles[:limit]:
                title = art.get("title", "Hackathon")
                url = art.get("url", "https://dev.to")
                summary = strip_html_tags(art.get("description", ""))[:220]
                pub_date = str(art.get("published_at") or "")[:10]

                items.append({
                    "id": f"devto_{art.get('id', title[:16])}",
                    "title": title,
                    "summary": summary or f"Hackathon update on Dev.to: {title}.",
                    "link": url,
                    "url": url,
                    "source": "Dev.to API",
                    "category": "hackathon",
                    "type": "hackathon",
                    "date": "",
                    "date_str": pub_date,
                    "deadline": "See article",
                    "location": "",
                    "where": "",
                    "posted": datetime.now().strftime("%Y-%m-%d"),
                })
    except Exception as e:
        logger.warning(f"Error parsing Dev.to API: {e}")
    return items


def parse_commudle(raw_bytes: bytes, source_name: str, city: str = "hyderabad", limit: int = 10) -> List[Dict[str, Any]]:
    """Parses events from Commudle JSON API or HTML fallback."""
    try:
        raw_text = raw_bytes.decode("utf-8", errors="replace")
        items = []
        try:
            data = json.loads(raw_text)
            events = []
            if isinstance(data, list):
                events = data
            elif isinstance(data, dict):
                events = data.get("data") or data.get("events") or []
            for ev in events[:limit]:
                title = ev.get("title") or ev.get("name") or "Commudle Event"
                slug = ev.get("slug") or ev.get("id") or ""
                link = f"https://www.commudle.com/events/{slug}" if slug else "https://www.commudle.com"
                items.append({
                    "title": title,
                    "url": link,
                    "link": link,
                    "source": source_name,
                    "category": "event",
                    "city": city,
                    "date": str(ev.get("start_date") or ev.get("date") or "")[:10],
                    "location": ev.get("venue") or city.capitalize(),
                    "summary": ev.get("description") or f"Event by {source_name}"
                })
            if items:
                return items
        except Exception:
            pass

        # HTML fallback
        matches = re.findall(r'<a[^>]+href=["\'](/events/[^"\']+)["\'][^>]*>([\s\S]*?)</a>', raw_text, re.IGNORECASE)
        for href, text in matches[:limit]:
            clean_title = strip_html_tags(text).strip()
            if clean_title and len(clean_title) > 3:
                link = f"https://www.commudle.com{href}" if href.startswith("/") else href
                items.append({
                    "title": clean_title,
                    "url": link,
                    "link": link,
                    "source": source_name,
                    "category": "event",
                    "city": city,
                    "date": "",
                    "location": city.capitalize(),
                    "summary": f"Commudle community event: {clean_title}"
                })
        return items
    except Exception:
        return []


# ==============================================================================
# COLLECTION DISPATCHER
# ==============================================================================

def collect(source: Optional[Any] = None, limit: int = 10) -> List[Dict[str, Any]]:
    """
    Collects raw items from a specific source (by key or dict) or all sources.
    Tracks per-source health and alerts after 3 consecutive zero-item runs.
    """
    if source is not None:
        if isinstance(source, str):
            cfg = SOURCES.get(source)
            if not cfg:
                return []
        elif isinstance(source, dict):
            cfg = source
        else:
            return []
        targets = [cfg]
    else:
        targets = [cfg for key, cfg in SOURCES.items() if key not in REMOVED_SOURCES]

    collected: List[Dict[str, Any]] = []
    for cfg in targets:
        url = cfg.get("url", "")
        name = cfg.get("name", "Unknown Source")
        stype = cfg.get("type", "xml")
        cat = cfg.get("category", "news")
        headers = cfg.get("headers")
        source_city = cfg.get("city", "hyderabad")

        try:
            raw, err = fetch_raw(url, headers=headers)
            items = []
            if raw:
                if stype == "xml":
                    items = parse_xml_feed(raw, name, cat, limit=limit)
                elif stype == "ical":
                    items = parse_ical(raw.decode("utf-8", errors="ignore"), name, limit=limit, city=source_city)
                elif stype == "devpost_api":
                    items = parse_devpost_api(raw, limit=limit)
                elif stype == "remoteok_api":
                    items = parse_remoteok_api(raw, limit=limit)
                elif stype == "bevy_api":
                    items = parse_bevy_api(raw, name, limit=limit, city=source_city)
                elif stype == "luma":
                    items = parse_luma(raw.decode("utf-8", errors="ignore"), name, limit=limit, city=source_city)
                elif stype == "commudle":
                    items = parse_commudle(raw, name, city=source_city, limit=limit)
                elif stype == "hn_api":
                    items = parse_hn_api(raw, limit=limit)
                elif stype == "devto_api":
                    items = parse_devto_api(raw, limit=limit)

            # Per-source health tracking: alert if 0 items for 3 consecutive runs
            try:
                import db
                import telegram_bot
                zero_runs = db.update_source_health(name, len(items))
                if zero_runs >= 3:
                    logger.warning(f"Source '{name}' returned 0 items for {zero_runs} consecutive runs!")
                    telegram_bot.send_health_alert(f"⚠️ Source Health Alert: {name} returned 0 items for {zero_runs} consecutive runs.")
            except Exception as e:
                logger.debug(f"Source health update error: {e}")

            today = datetime.now().strftime("%Y-%m-%d")
            for it in items:
                it["city"] = cfg.get("city", "online")
                it_date = it.get("date") or ""
                if it_date and it_date < today:
                    continue
                collected.append(it)
        except Exception as e:
            logger.warning(f"Error collecting from {name}: {e}")
    return collected


# ==============================================================================
# HEALTH TABLE RUNNER
# ==============================================================================

def run_health_check() -> List[Dict[str, Any]]:
    """
    Tests every source in SOURCES and returns detailed health metrics.
    """
    results = []
    for key, cfg in SOURCES.items():
        name = cfg["name"]
        url = cfg["url"]
        stype = cfg["type"]
        headers = cfg.get("headers")

        t0 = time.time()
        raw, err = fetch_raw(url, headers=headers, timeout=10)
        elapsed = time.time() - t0

        if raw is not None:
            size_bytes = len(raw)
            status = "OK"
            details = f"200 OK ({size_bytes} B)"
        else:
            size_bytes = 0
            status = "FAIL"
            details = err or "Unknown Error"

        results.append({
            "key": key,
            "name": name,
            "url": url,
            "type": stype,
            "status": status,
            "size_bytes": size_bytes,
            "elapsed_s": elapsed,
            "details": details,
        })
    return results


def print_health_table(results: List[Dict[str, Any]]):
    """Prints unsummarized Markdown health table."""
    print("### 🛡️ Legion Feed Collectors Health Table (34 Sources)")
    print()
    print("| # | Source Name | Type | Status | Size | Time | Details |")
    print("|---|-------------|------|--------|------|------|---------|")
    for i, r in enumerate(results, 1):
        if r["size_bytes"] >= 1024 * 1024:
            size_str = f"{r['size_bytes'] / (1024 * 1024):.1f} MB"
        elif r["size_bytes"] >= 1024:
            size_str = f"{r['size_bytes'] / 1024:.1f} KB"
        else:
            size_str = f"{r['size_bytes']} B"

        status_emoji = "✅ OK" if r["status"] == "OK" else "❌ FAIL"
        time_str = f"{r['elapsed_s']:.2f}s"
        print(f"| {i} | {r['name']} | `{r['type']}` | {status_emoji} | {size_str} | {time_str} | {r['details']} |")


# ==============================================================================
# CLI
# ==============================================================================

def main():
    parser = argparse.ArgumentParser(description="Legion Collectors 2.0 & Health Monitor")
    parser.add_argument("--health", action="store_true", help="Run full health table test")
    parser.add_argument("--show", action="store_true", help="Collect and show accepted opportunities passing quality gate")
    parser.add_argument("--out", type=str, default="", help="Export collected and gated opportunities to JSON file (e.g. posts.json)")
    parser.add_argument("--limit", type=int, default=5, help="Items per feed")
    args = parser.parse_args()

    # If --show is passed
    if args.show:
        print("Fetching opportunities across sources and filtering with Quality Gate...")
        raw_items = collect(limit=args.limit)
        accepted_items = []
        review_items = []
        rejected_count = 0

        for item in raw_items:
            decision, reason, clean = quality_gate.gate(item)
            if decision == "accept":
                accepted_items.append(clean)
            elif decision == "review":
                review_items.append((reason, clean))
            else:
                rejected_count += 1

        print(f"\n=======================================================")
        print(f"ACCEPTED OPPORTUNITIES ({len(accepted_items)})")
        print(f"=======================================================")
        for i, it in enumerate(accepted_items, 1):
            badge = it.get("badge") or "None"
            date_val = it.get("date") or it.get("deadline") or "Ongoing"
            sum_text = it.get("summary", "")[:80] + "..." if len(it.get("summary", "")) > 80 else it.get("summary", "")
            print(f"[{i:02d}] [{it.get('category', 'news').upper()}] [{badge}] {it.get('title')}")
            print(f"     📅 Date: {date_val} | 📍 Loc: {it.get('location', 'Online')}")
            print(f"     📝 Summary ({len(it.get('summary', ''))} chars): {sum_text}")
            print(f"     🔗 Link: {it.get('url')}\n")

        print(f"\nSummary: {len(accepted_items)} accepted, {len(review_items)} for review, {rejected_count} rejected.")
        return

    # If --out is passed
    if args.out:
        print(f"Collecting opportunities and exporting to {args.out}...")
        raw_items = collect(limit=args.limit)
        exported_list = []
        seen_urls = set()

        for item in raw_items:
            decision, reason, clean = quality_gate.gate(item)
            if decision == "accept":
                url = clean.get("url") or clean.get("link") or ""
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                exported_list.append({
                    "id": clean.get("id") or f"c_{len(exported_list)+1}",
                    "type": clean.get("category") or clean.get("type") or "news",
                    "title": clean.get("title", ""),
                    "sum": clean.get("summary", ""),
                    "date": clean.get("date", ""),
                    "where": clean.get("location") or clean.get("where") or ("Hyderabad" if clean.get("badge") == "HYDERABAD - OFFLINE" else "Online"),
                    "badge": clean.get("badge") or "",
                    "src": clean.get("source", "Web Feed"),
                    "link": url,
                    "posted": clean.get("posted", datetime.now().strftime("%Y-%m-%d")),
                    "status": "pending"
                })

        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(exported_list, f, indent=2, ensure_ascii=False)
        print(f"✅ Exported {len(exported_list)} accepted opportunities to {args.out}")
        return

    # Default action: run health table
    results = run_health_check()
    print_health_table(results)


if __name__ == "__main__":
    main()
