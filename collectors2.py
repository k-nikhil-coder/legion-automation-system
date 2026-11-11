"""
collectors2.py - Legion Automation System 34 Sources Collector & Health Monitor
Provides resilient fetching, feed parsing (RSS/Atom, iCal, JSON API, Luma),
and health diagnostic table runner.
"""

import argparse
import concurrent.futures
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
import config

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

DEFAULT_TIMEOUT = 12
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
        "category": "news",
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
        "city": "hyderabad",
        "headers": {"User-Agent": DEFAULT_USER_AGENT},
    },
    "hydpy": {
        "name": "HydPy",
        "url": "https://www.meetup.com/hyderabad-python-user-group/events/ical/",
        "type": "ical",
        "category": "event",
        "city": "hyderabad",
        "headers": {"User-Agent": DEFAULT_USER_AGENT},
    },
    "react_hyderabad": {
        "name": "React Hyderabad",
        "url": "https://luma.com/reacthyderabad",
        "type": "luma",
        "category": "event",
        "city": "hyderabad",
        "headers": {"User-Agent": DEFAULT_USER_AGENT},
    },
    "aws_hyderabad": {
        "name": "AWS User Group Hyderabad",
        "url": "https://www.meetup.com/awsughyd/events/ical/",
        "type": "ical",
        "category": "event",
        "city": "hyderabad",
        "headers": {"User-Agent": DEFAULT_USER_AGENT},
    },
    "ai_community_hyderabad": {
        "name": "AI Community Hyderabad",
        "url": "https://www.meetup.com/hyderabad-artificial-intelligence-meetup-group/events/ical/",
        "type": "ical",
        "category": "event",
        "city": "hyderabad",
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
    "luma_bengaluru": {
        "name": "Luma Bengaluru",
        "url": "https://luma.com/bengaluru",
        "type": "luma",
        "category": "event",
        "city": "bengaluru",
        "headers": {"User-Agent": DEFAULT_USER_AGENT},
    },
    "luma_hyderabad": {
        "name": "Luma Hyderabad",
        "url": "https://luma.com/hyderabad",
        "type": "luma",
        "category": "event",
        "city": "hyderabad",
        "headers": {"User-Agent": DEFAULT_USER_AGENT},
    },
    "unstop_hackathons": {
        "name": "Unstop Hackathons",
        "url": "https://unstop.com/api/public/opportunity/search-result?opportunity=hackathons&page=1&per_page=10",
        "type": "unstop_api",
        "category": "hackathon",
        "city": "online",
        "headers": {"User-Agent": DEFAULT_USER_AGENT, "Accept": "application/json"},
    },
}

# Feeds that have failed or were deprecated by upstream providers
REMOVED_SOURCES: Dict[str, str] = {
    "remoteok_rss": "RemoteOK discontinued RSS syndication with HTTP 410 Gone; use RemoteOK API instead.",
    "pycon_india": "PyCon India discontinued blog RSS endpoint with HTTP 404; active updates moved to social/conference site.",
    "hasgeek": "Hasgeek RSS feed endpoint returns HTTP 404 Not Found.",
    "commudle_bengaluru": "Commudle v1 API returns HTTP 503 Cloudflare challenge to non-browser clients.",
    "commudle_hyderabad": "Commudle v1 API returns HTTP 503 Cloudflare challenge to non-browser clients.",
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


def parse_xml_feed(raw_bytes: bytes, source_name: str, category: str, limit: int = 10, default_location: str = "Online") -> List[Dict[str, Any]]:
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
            summary = strip_html_tags(summary_el.text)[:800] if (summary_el is not None and summary_el.text) else ""
            date_el = _find_first(entry, [f"{atom_ns}updated", f"{atom_ns}published", "updated", "published"])
            raw_date = date_el.text.strip() if (date_el is not None and date_el.text) else ""
            clean_date = ""
            if category in ("event", "events", "hackathon", "hackathons", "workshops"):
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
                    "location": default_location,
                    "where": default_location,
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
        summary = strip_html_tags(desc_el.text)[:800] if (desc_el is not None and desc_el.text) else ""
        date_el = it.find("pubDate")
        raw_date = date_el.text.strip() if (date_el is not None and date_el.text) else ""
        clean_date = ""
        if category in ("event", "events", "hackathon", "hackathons", "workshops"):
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
                "location": default_location,
                "where": default_location,
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
            "summary": strip_html_tags(summary)[:800] or f"Meetup hosted by {source_name}: {title}",
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
            summary = strip_html_tags(f"Online developer hackathon on Devpost. {themes_str}{loc_str}.")[:800]
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
                desc = strip_html_tags(str(job.get("description") or ""))[:800]
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
    today_str = config.now_ist().strftime("%Y-%m-%d")
    target_city_lower = (city or "").lower()
    try:
        j = json.loads(data.decode("utf-8", errors="ignore"))
        results = j.get("results", [])
        for ev in results[:limit * 2]:
            title = ev.get("title", "Community Event")
            raw_url = ev.get("url") or ""
            url = raw_url if raw_url.startswith("http") else f"https://gdg.community.dev{raw_url}"
            summary = strip_html_tags(ev.get("description_short") or ev.get("description") or "")[:800]
            start_date = str(ev.get("start_date") or "")
            clean_date = start_date[:10] if len(start_date) >= 10 and "-" in start_date[:10] else ""

            # Filter out past events
            if clean_date and clean_date < today_str:
                continue

            # Guard against Bevy API returning non-Indian chapter events (e.g. -05:00 Peru DevFests)
            if target_city_lower in ("hyderabad", "bengaluru"):
                tz_mismatch = re.search(r'[+-](?!05:30)\d{2}:\d{2}$', start_date.strip())
                chapter_city = str((ev.get("chapter") or {}).get("city") or ev.get("venue_city") or "").lower()
                if tz_mismatch and target_city_lower not in (title + " " + chapter_city).lower():
                    continue
                if chapter_city and config.normalize_city(chapter_city) not in (target_city_lower, "other"):
                    continue

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
                "event_start": start_date,
                "date_str": start_date,
                "deadline": "RSVP on Chapter Page",
                "location": loc_val,
                "where": loc_val,
                "posted": today_str,
            })
            if len(items) >= limit:
                break
    except Exception as e:
        logger.warning(f"Error parsing Bevy API: {e}")
    return items


def parse_luma(html_text: str, source_name: str, limit: int = 10, city: str = "hyderabad") -> List[Dict[str, Any]]:
    """Parses Luma calendar page HTML via __NEXT_DATA__ or JSON-LD fallback."""
    items: List[Dict[str, Any]] = []
    today_str = config.now_ist().strftime("%Y-%m-%d")
    try:
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html_text)
        if m:
            data = json.loads(m.group(1))
            inner = data.get("props", {}).get("pageProps", {}).get("initialData", {}).get("data", {})
            events = (
                inner.get("upcoming", {}).get("entries", [])
                or inner.get("events", [])
                or inner.get("featured_items", [])
            )
            for it in events[:limit]:
                ev = it.get("event", it) if isinstance(it, dict) else {}
                title = ev.get("name", "Tech Meetup")
                slug = ev.get("url", "")
                url = slug if str(slug).startswith("http") else (f"https://luma.com/{slug}" if slug else "https://luma.com")
                summary = strip_html_tags(ev.get("description", ""))[:800]
                start_at = str(ev.get("start_at", ""))
                clean_date = start_at[:10] if len(start_at) >= 10 and "-" in start_at[:10] else ""
                if clean_date and clean_date < today_str:
                    continue

                geo_info = ev.get("geo_address_info") or {}
                venue_name = geo_info.get("address") or geo_info.get("city_state") or ""
                loc_val = venue_name or (city.capitalize() if city else "Online")
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
                    "venue": venue_name,
                    "date": clean_date,
                    "event_start": start_at,
                    "date_str": start_at,
                    "deadline": "RSVP on Luma",
                    "location": loc_val,
                    "where": loc_val,
                    "posted": today_str,
                })

        # Fallback to JSON-LD Event blocks if __NEXT_DATA__ yields none
        if not items:
            ld_blocks = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>([\s\S]*?)</script>', html_text, re.I)
            for raw_ld in ld_blocks:
                try:
                    obj = json.loads(raw_ld.strip())
                    objs = obj if isinstance(obj, list) else (obj.get("@graph", [obj]) if isinstance(obj, dict) else [])
                    for o in objs:
                        if isinstance(o, dict) and str(o.get("@type", "")).lower() == "event":
                            title = o.get("name", "Tech Meetup")
                            url = o.get("url") or "https://luma.com"
                            start_at = str(o.get("startDate") or "")
                            clean_date = start_at[:10] if len(start_at) >= 10 and "-" in start_at[:10] else ""
                            if clean_date and clean_date < today_str:
                                continue
                            loc_val = city.capitalize() if city else "Online"
                            items.append({
                                "id": f"luma_{title[:16]}",
                                "title": title,
                                "summary": strip_html_tags(o.get("description") or "")[:800] or f"Event hosted by {source_name}: {title}.",
                                "link": url,
                                "url": url,
                                "source": source_name,
                                "category": "event",
                                "type": "event",
                                "city": city,
                                "date": clean_date,
                                "event_start": start_at,
                                "date_str": start_at,
                                "deadline": "RSVP on Luma",
                                "location": loc_val,
                                "where": loc_val,
                                "posted": today_str,
                            })
                            if len(items) >= limit:
                                break
                except Exception:
                    pass
    except Exception as e:
        logger.warning(f"Error parsing Luma page: {e}")
    return items


def parse_unstop_api(data: bytes, limit: int = 10) -> List[Dict[str, Any]]:
    """Parses Unstop public opportunity search JSON API into normalized hackathon dicts."""
    items: List[Dict[str, Any]] = []
    today_str = config.now_ist().strftime("%Y-%m-%d")
    try:
        j = json.loads(data.decode("utf-8", errors="ignore"))
        rows = j.get("data", {}).get("data", [])
        for r in rows[:limit * 2]:
            title = str(r.get("title") or "").strip()
            pub_url = str(r.get("public_url") or "").strip()
            if not title or not pub_url:
                continue
            url = pub_url if pub_url.startswith("http") else f"https://unstop.com/{pub_url.lstrip('/')}"
            end_date = str(r.get("end_date") or "")[:10]
            start_date = str(r.get("start_date") or "")[:10]
            clean_date = end_date or start_date
            if clean_date and clean_date < today_str:
                continue
            region = str(r.get("region") or "online").lower()
            details = strip_html_tags(str(r.get("details") or ""))[:800]
            org_name = str((r.get("organisation") or {}).get("name") or "")
            items.append({
                "id": f"unstop_{r.get('id', title[:16])}",
                "title": title,
                "organizer": org_name,
                "summary": details or f"Tech hackathon on Unstop{' by ' + org_name if org_name else ''}: {title}.",
                "link": url,
                "url": url,
                "source": "Unstop Hackathons",
                "category": "hackathon",
                "type": "hackathon",
                "mode": "ONLINE" if "online" in region else "OFFLINE",
                "location": "Online" if "online" in region else "India",
                "date": clean_date,
                "event_start": start_date,
                "deadline": end_date or start_date or "Check Unstop",
                "posted": today_str,
            })
            if len(items) >= limit:
                break
    except Exception as e:
        logger.warning(f"Error parsing Unstop API: {e}")
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
                summary = strip_html_tags(art.get("description", ""))[:800]
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
# SPECIALIZED COLLECTORS v3.0 (ASD-STE100)
# ==============================================================================

def collect_luma_events(city: str = "hyderabad") -> List[Dict[str, Any]]:
    """
    Fetches local events from Luma by parsing __NEXT_DATA__ or JSON-LD from https://luma.com/{city}.
    Supports hyderabad and bengaluru.
    """
    cities = [city] if city else ["hyderabad", "bengaluru"]
    if city in ("all", "both"):
        cities = ["hyderabad", "bengaluru"]

    items: List[Dict[str, Any]] = []
    for c in cities:
        url = f"https://luma.com/{c}"
        try:
            raw, err = fetch_raw(url, headers={"User-Agent": DEFAULT_USER_AGENT}, timeout=DEFAULT_TIMEOUT)
            if not raw:
                continue
            parsed = parse_luma(raw.decode("utf-8", errors="ignore"), f"Luma {c.capitalize()}", limit=10, city=c)
            items.extend(parsed)
        except Exception as e:
            logger.warning(f"Error in collect_luma_events({c}): {e}")
    return items


def collect_devfolio_hackathons() -> List[Dict[str, Any]]:
    """
    Fetches Indian hackathons from Devfolio API/Next.js or Unstop fallback.
    """
    items: List[Dict[str, Any]] = []
    today_str = config.now_ist().strftime("%Y-%m-%d")
    api_url = "https://api.devfolio.co/api/hackathons?filter=upcoming&page=1&per_page=15"
    try:
        raw, err = fetch_raw(api_url, headers={"User-Agent": DEFAULT_USER_AGENT, "Accept": "application/json"}, timeout=DEFAULT_TIMEOUT)
        if raw:
            data = json.loads(raw.decode("utf-8", errors="ignore"))
            hackathons = data.get("result", []) or data.get("hackathons", [])
            for h in hackathons[:15]:
                title = h.get("name") or h.get("title", "Devfolio Hackathon")
                slug = h.get("slug", "")
                url = f"https://{slug}.devfolio.co" if slug else "https://devfolio.co/hackathons"
                desc = strip_html_tags(h.get("desc") or h.get("tagline") or "")[:800]
                start_date = str(h.get("starts_at") or "")[:10]
                end_date = str(h.get("ends_at") or "")[:10]
                if (end_date or start_date) and (end_date or start_date) < today_str:
                    continue
                prizes = str(h.get("prize_pool") or h.get("prizes", ""))
                mode = "ONLINE" if h.get("is_online", True) else "OFFLINE"
                themes = ", ".join(h.get("themes", [])) if isinstance(h.get("themes"), list) else ""

                items.append({
                    "id": f"devfolio_{slug or title[:16]}",
                    "title": title,
                    "summary": desc or f"Indian tech hackathon on Devfolio. Themes: {themes}. Prizes: {prizes}",
                    "link": url,
                    "url": url,
                    "apply_url": url,
                    "source": "Devfolio",
                    "category": "hackathon",
                    "type": "hackathon",
                    "date": start_date,
                    "event_start": start_date,
                    "date_str": start_date,
                    "deadline": end_date or start_date or "Open",
                    "location": "Online" if mode == "ONLINE" else "Offline",
                    "mode": mode,
                    "prize": prizes,
                    "skill_tags": themes,
                    "posted": today_str,
                })
            if items:
                return items
    except Exception as e:
        logger.debug(f"Devfolio API error: {e}")

    # 2. Fallback to scraping __NEXT_DATA__ from /hackathons
    try:
        raw, err = fetch_raw("https://devfolio.co/hackathons", headers={"User-Agent": DEFAULT_USER_AGENT}, timeout=DEFAULT_TIMEOUT)
        if raw:
            html_txt = raw.decode("utf-8", errors="ignore")
            m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html_txt)
            if m:
                d = json.loads(m.group(1))
                hacks = d.get("props", {}).get("pageProps", {}).get("hackathons", []) or d.get("props", {}).get("pageProps", {}).get("openHackathons", [])
                for h in hacks[:10]:
                    title = h.get("name") or "Devfolio Hackathon"
                    slug = h.get("slug", "")
                    url = f"https://{slug}.devfolio.co" if slug else "https://devfolio.co/hackathons"
                    s_date = str(h.get("starts_at") or "")[:10]
                    if s_date and s_date < today_str:
                        continue
                    items.append({
                        "id": f"devfolio_{slug or title[:16]}",
                        "title": title,
                        "summary": strip_html_tags(h.get("desc") or "")[:800] or "Devfolio Indian developer hackathon",
                        "link": url,
                        "url": url,
                        "source": "Devfolio",
                        "category": "hackathon",
                        "type": "hackathon",
                        "date": s_date,
                        "event_start": s_date,
                        "deadline": str(h.get("ends_at") or s_date or "Open")[:10],
                        "posted": today_str,
                    })
    except Exception as e:
        logger.warning(f"Error in collect_devfolio_hackathons: {e}")
    return items


def collect_foss_united() -> List[Dict[str, Any]]:
    """
    Fetches upcoming open-source community meetups from FOSS United.
    Extracts dates from anchor text to drop past meetups and resolves city accurately.
    """
    items: List[Dict[str, Any]] = []
    today_dt = config.now_ist().date()
    today_str = today_dt.strftime("%Y-%m-%d")
    targets = [
        ("https://fossunited.org/events", "FOSS United Events"),
        ("https://fossunited.org/c/hyderabad", "FOSS United Hyderabad"),
        ("https://fossunited.org/c/bengaluru", "FOSS United Bengaluru"),
    ]
    date_pattern = re.compile(r'\b(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(20\d{2})\b', re.I)

    for url, source_name in targets:
        target_items = 0
        try:
            raw, err = fetch_raw(url, headers={"User-Agent": DEFAULT_USER_AGENT}, timeout=DEFAULT_TIMEOUT)
            if not raw:
                continue
            html_txt = raw.decode("utf-8", errors="ignore")
            blocks = re.findall(r'<a[^>]+href=["\'](/[^"\']+|https?://[^"\']+)["\'][^>]*>([\s\S]*?)</a>', html_txt)
            for href, anchor_txt in blocks:
                clean_t = strip_html_tags(anchor_txt).strip()
                if clean_t.startswith(("http://", "https://")) or len(clean_t) <= 10 or not any(w in clean_t.lower() for w in ("meetup", "hackathon", "conference", "workshop", "foss")):
                    continue

                # Parse date from FOSS United card text to filter past events (e.g., "20 Jun 2026")
                parsed_date = ""
                m_dt = date_pattern.search(clean_t)
                if m_dt:
                    try:
                        dt_obj = datetime.strptime(f"{m_dt.group(1)} {m_dt.group(2)[:3]} {m_dt.group(3)}", "%d %b %Y")
                        parsed_date = dt_obj.strftime("%Y-%m-%d")
                        if dt_obj.date() < today_dt:
                            continue
                    except Exception:
                        pass

                full_link = href if href.startswith("http") else f"https://fossunited.org{href}"
                text_ctx = f"{clean_t} {url}".lower()
                if "hyderabad" in text_ctx:
                    city_val = "hyderabad"
                elif "bengaluru" in text_ctx or "bangalore" in text_ctx:
                    city_val = "bengaluru"
                elif "online" in text_ctx or "virtual" in text_ctx:
                    city_val = "online"
                else:
                    city_val = "other"

                items.append({
                    "id": f"fossunited_{clean_t[:16]}",
                    "title": clean_t,
                    "summary": f"FOSS United community event for open-source developers: {clean_t}",
                    "link": full_link,
                    "url": full_link,
                    "source": source_name,
                    "category": "event",
                    "type": "event",
                    "city": city_val,
                    "date": parsed_date,
                    "event_start": parsed_date,
                    "deadline": parsed_date or "Check event page",
                    "location": city_val.capitalize() if city_val != "other" else clean_t,
                    "posted": today_str,
                })
                target_items += 1
                if target_items >= 5:
                    break
        except Exception as e:
            logger.warning(f"Error in collect_foss_united({url}): {e}")
    return items


def collect_ats_jobs() -> List[Dict[str, Any]]:
    """
    Fetches direct early-career tech job openings from Greenhouse and Lever APIs.
    Prioritizes Indian tech hubs (Hyderabad, Bengaluru, India) and filters out senior/sales roles.
    """
    items: List[Dict[str, Any]] = []
    gh_boards = ["razorpay", "swiggy", "postman", "gitlab", "stripe"]
    lever_companies = ["cred", "atlassian", "clevertap"]

    loc_keywords = re.compile(r'\b(hyderabad|bengaluru|bangalore|india|remote)\b', re.IGNORECASE)
    today_str = config.now_ist().strftime("%Y-%m-%d")

    # 1. Greenhouse boards
    for board in gh_boards:
        board_count = 0
        try:
            url = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"
            raw, err = fetch_raw(url, headers={"User-Agent": DEFAULT_USER_AGENT}, timeout=DEFAULT_TIMEOUT)
            if not raw:
                continue
            data = json.loads(raw.decode("utf-8", errors="ignore"))
            jobs = data.get("jobs", [])
            for job in jobs:
                loc_name = (job.get("location") or {}).get("name", "")
                if not loc_keywords.search(loc_name):
                    continue
                title = job.get("title", "Software Engineer")
                if quality_gate.SENIOR_REJECT_REGEX.search(title) or quality_gate.NON_TECH_ROLE_REGEX.search(title):
                    continue
                if quality_gate.GEO_RESTRICTED_REMOTE_REGEX.search(f"{title} {loc_name}"):
                    continue
                if not quality_gate.TECH.search(f"{title} {job.get('content', '')[:400]}"):
                    continue

                job_url = job.get("absolute_url") or f"https://boards.greenhouse.io/{board}"
                desc = strip_html_tags(job.get("content") or "")[:800]
                city = "hyderabad" if "hyderabad" in loc_name.lower() else ("bengaluru" if "bengaluru" in loc_name.lower() or "bangalore" in loc_name.lower() else "online")

                items.append({
                    "id": f"gh_{job.get('id', title[:16])}",
                    "title": f"{title} at {board.capitalize()}",
                    "company": board.capitalize(),
                    "role": title,
                    "summary": desc or f"Direct tech opening at {board.capitalize()}: {title} in {loc_name}.",
                    "link": job_url,
                    "url": job_url,
                    "apply_url": job_url,
                    "source": f"Greenhouse ({board.capitalize()})",
                    "category": "job",
                    "type": "job",
                    "location": loc_name or "Remote / India",
                    "city": city,
                    "posted": today_str,
                })
                board_count += 1
                if board_count >= 5:
                    break
        except Exception as e:
            logger.debug(f"Greenhouse collect error ({board}): {e}")

    # 2. Lever companies
    for comp in lever_companies:
        comp_count = 0
        try:
            url = f"https://api.lever.co/v0/postings/{comp}?mode=json"
            raw, err = fetch_raw(url, headers={"User-Agent": DEFAULT_USER_AGENT}, timeout=DEFAULT_TIMEOUT)
            if not raw:
                continue
            data = json.loads(raw.decode("utf-8", errors="ignore"))
            if isinstance(data, list):
                for job in data:
                    categories = job.get("categories", {})
                    loc_name = categories.get("location", "")
                    if not loc_keywords.search(loc_name):
                        continue
                    title = job.get("text", "Software Engineer")
                    if quality_gate.SENIOR_REJECT_REGEX.search(title) or quality_gate.NON_TECH_ROLE_REGEX.search(title):
                        continue
                    if quality_gate.GEO_RESTRICTED_REMOTE_REGEX.search(f"{title} {loc_name}"):
                        continue
                    job_url = job.get("hostedUrl") or job.get("applyUrl") or ""
                    desc = strip_html_tags(job.get("descriptionPlain") or "")[:800]
                    if not quality_gate.TECH.search(f"{title} {desc[:400]}"):
                        continue
                    city = "hyderabad" if "hyderabad" in loc_name.lower() else ("bengaluru" if "bengaluru" in loc_name.lower() or "bangalore" in loc_name.lower() else "online")

                    items.append({
                        "id": f"lever_{job.get('id', title[:16])}",
                        "title": f"{title} at {comp.capitalize()}",
                        "company": comp.capitalize(),
                        "role": title,
                        "summary": desc or f"Direct tech opening at {comp.capitalize()}: {title} in {loc_name}.",
                        "link": job_url,
                        "url": job_url,
                        "apply_url": job_url,
                        "source": f"Lever ({comp.capitalize()})",
                        "category": "job",
                        "type": "job",
                        "location": loc_name or "Remote / India",
                        "city": city,
                        "posted": today_str,
                    })
                    comp_count += 1
                    if comp_count >= 5:
                        break
        except Exception as e:
            logger.debug(f"Lever collect error ({comp}): {e}")

    return items


def collect_search_rss_jobs() -> List[Dict[str, Any]]:
    """
    Uses Google News RSS feeds with target search queries for fresher/intern hiring posts
    across LinkedIn, Naukri, Internshala, and Foundit in Hyderabad and Bengaluru.
    Preserves true job city without hardcoding 'Online'.
    """
    items: List[Dict[str, Any]] = []
    queries = [
        ("hyderabad", "software engineer fresher intern hyderabad site:linkedin.com OR site:naukri.com OR site:internshala.com"),
        ("bengaluru", "software engineer intern bengaluru fresher site:internshala.com OR site:linkedin.com OR site:naukri.com"),
    ]
    for target_city, q in queries:
        encoded_q = urllib.parse.quote(q)
        rss_url = f"https://news.google.com/rss/search?q={encoded_q}&hl=en-IN&gl=IN&ceid=IN:en"
        try:
            raw, err = fetch_raw(rss_url, headers={"User-Agent": DEFAULT_USER_AGENT}, timeout=DEFAULT_TIMEOUT)
            if not raw:
                continue
            parsed = parse_xml_feed(raw, "Search Jobs RSS", "job", limit=6, default_location="")
            for it in parsed:
                t_low = it.get("title", "").lower()
                if "hyderabad" in t_low:
                    it["city"] = "hyderabad"
                    it["location"] = "Hyderabad"
                elif "bengaluru" in t_low or "bangalore" in t_low:
                    it["city"] = "bengaluru"
                    it["location"] = "Bengaluru"
                elif "work from home" in t_low or "remote" in t_low:
                    it["city"] = "online"
                    it["location"] = "Remote / Work From Home"
                else:
                    it["city"] = None
                    it["location"] = ""
            items.extend(parsed)
        except Exception as e:
            logger.warning(f"Error in collect_search_rss_jobs query '{q[:30]}': {e}")
    return items


def collect_jobspy_sidecar() -> List[Dict[str, Any]]:
    """
    Scrapes jobs via jobspy if installed; otherwise logs info and returns empty list.
    """
    try:
        from jobspy import scrape_jobs
    except ImportError:
        logger.info("jobspy not installed; skipping collect_jobspy_sidecar")
        return []

    try:
        jobs = scrape_jobs(
            site_name=["linkedin", "indeed"],
            search_term="software engineer",
            location="Hyderabad, India",
            results_wanted=5,
            hours_old=72,
        )
        items: List[Dict[str, Any]] = []
        if jobs is not None and not jobs.empty:
            for _, r in jobs.iterrows():
                t = str(r.get("title") or "Software Engineer")
                comp = str(r.get("company") or "Tech Company")
                u = str(r.get("job_url") or "")
                desc = strip_html_tags(str(r.get("description") or ""))[:800]
                loc = str(r.get("location") or "Hyderabad")
                items.append({
                    "id": f"jobspy_{u[-16:] if u else t[:16]}",
                    "title": f"{t} at {comp}",
                    "company": comp,
                    "role": t,
                    "summary": desc or f"Software engineering role at {comp}.",
                    "link": u,
                    "url": u,
                    "source": "JobSpy Sidecar",
                    "category": "job",
                    "type": "job",
                    "location": loc,
                    "city": "hyderabad",
                    "posted": datetime.now().strftime("%Y-%m-%d"),
                })
        return items
    except Exception as e:
        logger.warning(f"Error running jobspy: {e}")
        return []


def collect_courses_and_certs() -> List[Dict[str, Any]]:
    """
    Fetches recent tech learning opportunities from DeepLearning.AI,
    Hugging Face, and cloud provider feeds. Routes general research/dataset posts to news.
    """
    items: List[Dict[str, Any]] = []
    feeds = [
        ("https://www.deeplearning.ai/the-batch/feed/", "DeepLearning.AI The Batch", "course"),
        ("https://huggingface.co/blog/feed.xml", "Hugging Face Blog & Courses", "news"),
        ("https://aws.amazon.com/blogs/training-and-certification/feed/", "AWS Training & Certs", "course"),
    ]
    for url, name, cat in feeds:
        try:
            raw, err = fetch_raw(url, headers={"User-Agent": DEFAULT_USER_AGENT}, timeout=DEFAULT_TIMEOUT)
            if not raw:
                continue
            parsed = parse_xml_feed(raw, name, cat, limit=4)
            for it in parsed:
                text_low = f"{it.get('title', '')} {it.get('summary', '')}".lower()
                if any(w in text_low for w in ("workshop", "bootcamp", "hands-on", "webinar", "live coding")):
                    it["category"] = "workshops"
                    it["type"] = "workshops"
                elif any(w in text_low for w in ("course", "certification", "training", "credential", "short course")):
                    it["category"] = "course"
                    it["type"] = "course"
            items.extend(parsed)
        except Exception as e:
            logger.debug(f"Error fetching course feed {name}: {e}")
    return items


ALL_COLLECTORS = [
    collect_luma_events,
    collect_devfolio_hackathons,
    collect_foss_united,
    collect_ats_jobs,
    collect_search_rss_jobs,
    collect_jobspy_sidecar,
    collect_courses_and_certs,
]


# ==============================================================================
# COLLECTION DISPATCHER
# ==============================================================================

def _fetch_single_source(cfg: Dict[str, Any], limit: int) -> Tuple[Dict[str, Any], List[Dict[str, Any]], str]:
    """Worker helper to fetch and parse one configured source without touching SQLite."""
    url = cfg.get("url", "")
    name = cfg.get("name", "Unknown Source")
    stype = cfg.get("type", "xml")
    cat = cfg.get("category", "news")
    headers = cfg.get("headers")
    source_city = cfg.get("city", "hyderabad")

    items: List[Dict[str, Any]] = []
    health_status = "FAIL"
    try:
        raw, err = fetch_raw(url, headers=headers)
        if raw:
            health_status = "OK"
            if stype == "xml":
                items = parse_xml_feed(raw, name, cat, limit=limit)
            elif stype == "ical":
                decoded_ical = raw.decode("utf-8", errors="ignore")
                items = parse_ical(decoded_ical, name, limit=limit, city=source_city)
                if not items and "BEGIN:VCALENDAR" in decoded_ical:
                    health_status = "EMPTY_OK"
            elif stype == "devpost_api":
                items = parse_devpost_api(raw, limit=limit)
            elif stype == "remoteok_api":
                items = parse_remoteok_api(raw, limit=limit)
            elif stype == "bevy_api":
                items = parse_bevy_api(raw, name, limit=limit, city=source_city)
            elif stype == "luma":
                decoded_luma = raw.decode("utf-8", errors="ignore")
                items = parse_luma(decoded_luma, name, limit=limit, city=source_city)
                if not items and "__NEXT_DATA__" in decoded_luma:
                    health_status = "EMPTY_OK"
            elif stype == "commudle":
                items = parse_commudle(raw, name, city=source_city, limit=limit)
            elif stype == "hn_api":
                items = parse_hn_api(raw, limit=limit)
            elif stype == "devto_api":
                items = parse_devto_api(raw, limit=limit)
            elif stype == "unstop_api":
                items = parse_unstop_api(raw, limit=limit)
        else:
            health_status = err or "FAIL"
    except Exception as e:
        logger.warning(f"Error collecting from {name}: {e}")
        health_status = f"ERR: {e}"
    return cfg, items, health_status


def collect(source: Optional[Any] = None, limit: int = 10) -> List[Dict[str, Any]]:
    """
    Collects raw items from a specific source (by key or dict) or all sources
    concurrently using a bounded thread pool.
    Tracks per-source health sequentially on main thread and alerts after 3
    consecutive failure runs (skipping valid empty iCal calendars).
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
    today = config.now_ist().strftime("%Y-%m-%d")

    def _record_and_append(cfg: Dict[str, Any], items: List[Dict[str, Any]], health_status: str):
        name = cfg.get("name", "Unknown Source")
        try:
            import db
            import telegram_bot
            zero_runs = db.update_source_health(name, len(items), status=health_status)
            if zero_runs >= 3:
                logger.warning(f"Source '{name}' returned 0 items for {zero_runs} consecutive runs ({health_status})!")
                telegram_bot.send_health_alert(f"⚠️ Source Health Alert: {name} returned 0 items for {zero_runs} consecutive runs.")
        except Exception as e:
            logger.debug(f"Source health update error: {e}")

        for it in items:
            if cfg.get("city") and not it.get("city"):
                it["city"] = cfg["city"]
            it_date = it.get("date") or ""
            if it_date and it_date < today:
                continue
            collected.append(it)

    if len(targets) <= 1 and source is not None:
        for cfg in targets:
            _record_and_append(*_fetch_single_source(cfg, limit))
        return collected

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        source_futures = [pool.submit(_fetch_single_source, cfg, limit) for cfg in targets]
        collector_futures = []
        if source is None:
            for fn in ALL_COLLECTORS:
                collector_futures.append((getattr(fn, "__name__", str(fn)), pool.submit(fn)))

        for fut in concurrent.futures.as_completed(source_futures):
            try:
                _record_and_append(*fut.result())
            except Exception as e:
                logger.warning(f"Error processing source future: {e}")

        for fn_name, fut in collector_futures:
            try:
                fn_items = fut.result()
                if fn_items:
                    collected.extend(fn_items)
            except Exception as e:
                logger.warning(f"Error executing collector {fn_name}: {e}")

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
        if key in REMOVED_SOURCES:
            continue
        name = cfg["name"]
        url = cfg["url"]
        stype = cfg["type"]
        headers = cfg.get("headers")

        t0 = time.time()
        raw, err = fetch_raw(url, headers=headers, timeout=DEFAULT_TIMEOUT)
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
