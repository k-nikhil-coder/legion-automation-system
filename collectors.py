"""
Legion Automation System - Content Collectors
Fetches opportunities from reliable public feeds (RSS, Atom, JSON APIs)
using the Python standard library with resilient timeouts and error handling.
"""

import html
import json
import logging
import re
import sys
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import List, Dict, Any, Optional

import config

# Ensure stdout handles UTF-8 on Windows
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

logger = logging.getLogger("legion.collectors")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s] %(message)s")

TAG_RE = re.compile(r"<[^>]+>")

def strip_html_tags(text: str) -> str:
    """Removes HTML tags, style/script blocks, comments, and unescapes entities."""
    if not text:
        return ""
    # Strip script and style blocks completely
    clean = re.sub(r"<(script|style)[^>]*>[\s\S]*?</\1>", " ", text, flags=re.IGNORECASE)
    # Strip XML/HTML comments
    clean = re.sub(r"<!--[\s\S]*?-->", " ", clean)
    # Replace block-level tags and breaks with spaces
    clean = re.sub(r"<(?:p|div|br|li|h[1-6]|tr|td)(?:\s+[^>]*>|>)", " ", clean, flags=re.IGNORECASE)
    # Strip remaining inline tags without extra whitespace insertion
    clean = TAG_RE.sub("", clean)
    clean = html.unescape(clean)
    return " ".join(clean.split())

def fetch_data(url: str, timeout: int = config.HTTP_TIMEOUT, headers: Optional[Dict[str, str]] = None) -> Optional[bytes]:
    """
    Resilient HTTP GET request using standard library.
    Returns bytes content or None on any error.
    """
    if not url:
        return None
    
    req_headers = {
        "User-Agent": config.USER_AGENT,
        "Accept": "application/rss+xml, application/atom+xml, application/xml, application/json, text/xml, */*",
        "Accept-Language": "en-US,en;q=0.9",
    }
    if headers:
        req_headers.update(headers)
        
    req = urllib.request.Request(url, headers=req_headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                return resp.read()
            else:
                logger.warning(f"Unexpected HTTP status {resp.status} from {url}")
                return None
    except urllib.error.HTTPError as e:
        logger.warning(f"HTTPError {e.code} ({e.reason}) fetching {url}")
        return None
    except urllib.error.URLError as e:
        logger.warning(f"URLError ({e.reason}) fetching {url}")
        return None
    except TimeoutError as e:
        logger.warning(f"Timeout fetching {url}: {e}")
        return None
    except Exception as e:
        logger.warning(f"Unexpected error fetching {url}: {e}")
        return None

def _first_element(parent: ET.Element, tags: List[str]) -> Optional[ET.Element]:
    """Helper to safely find the first matching XML element without element truth-value testing."""
    for tag in tags:
        el = parent.find(tag)
        if el is not None:
            return el
    return None

def parse_xml_feed(raw_bytes: bytes) -> List[Dict[str, str]]:
    """
    Parses RSS 2.0 or Atom XML bytes into a list of normalized entry dictionaries.
    Keys: title, link, summary, date_str
    """
    entries = []
    if not raw_bytes:
        return entries
    
    try:
        root = ET.fromstring(raw_bytes)
    except ET.ParseError as e:
        logger.warning(f"XML ParseError: {e}")
        return entries

    atom_ns = "{http://www.w3.org/2005/Atom}"
    atom_entries = root.findall(f".//{atom_ns}entry")
    if not atom_entries:
        atom_entries = root.findall(".//entry")
        
    if atom_entries:
        for entry in atom_entries:
            title_el = _first_element(entry, [f"{atom_ns}title", "title"])
            title = title_el.text.strip() if (title_el is not None and title_el.text) else "Untitled"
            
            link = ""
            link_candidates = entry.findall(f"{atom_ns}link") or entry.findall("link")
            for lk in link_candidates:
                href = lk.get("href") or lk.text
                rel = lk.get("rel", "alternate")
                if href and rel in ("alternate", ""):
                    link = href.strip()
                    break
            
            summary_el = _first_element(entry, [
                f"{atom_ns}summary", 
                f"{atom_ns}content", 
                "summary", 
                "content"
            ])
            summary = strip_html_tags(summary_el.text) if (summary_el is not None and summary_el.text) else ""
            
            date_el = _first_element(entry, [
                f"{atom_ns}updated", 
                f"{atom_ns}published", 
                "updated", 
                "published"
            ])
            date_str = date_el.text.strip() if (date_el is not None and date_el.text) else ""
            
            if title and link:
                entries.append({
                    "title": title,
                    "link": link,
                    "summary": summary,
                    "date_str": date_str
                })
        return entries

    # Check for RSS 2.0 (<rss><channel><item>...</item></channel></rss>)
    items = root.findall(".//item")
    for it in items:
        title_el = it.find("title")
        title = title_el.text.strip() if (title_el is not None and title_el.text) else "Untitled"
        
        link_el = it.find("link")
        link = link_el.text.strip() if (link_el is not None and link_el.text) else ""
        
        desc_el = it.find("description")
        summary = strip_html_tags(desc_el.text) if (desc_el is not None and desc_el.text) else ""
        
        date_el = it.find("pubDate")
        date_str = date_el.text.strip() if (date_el is not None and date_el.text) else ""
        
        if title and link:
            entries.append({
                "title": title,
                "link": link,
                "summary": summary,
                "date_str": date_str
            })
            
    return entries

def fetch_devpost_hackathons(limit: int = config.MAX_ITEMS_PER_FEED) -> List[Dict[str, Any]]:
    """
    Collects hackathon opportunities.
    Attempts Devpost RSS first, then falls back to Dev.to hackathons feed.
    """
    feed_cfg = config.FEEDS.get("hackathons", {})
    primary_url = feed_cfg.get("primary", "https://devpost.com/hackathons.rss")
    fallback_url = feed_cfg.get("fallback", "https://dev.to/feed/tag/hackathon")
    
    data = fetch_data(primary_url)
    source_name = "Devpost"
    
    if not data and fallback_url:
        logger.info(f"Devpost primary returned no data. Falling back to {fallback_url}")
        data = fetch_data(fallback_url)
        source_name = "Dev.to Hackathons"
        
    entries = parse_xml_feed(data) if data else []
    items = []
    for entry in entries[:limit]:
        title = entry["title"]
        url = entry["link"]
        summary = entry["summary"] or f"Join this exciting developer hackathon on {source_name}."
        
        items.append({
            "source": source_name,
            "category": "hackathon",
            "title": title,
            "url": url,
            "summary": summary,
            "date_str": entry.get("date_str", ""),
            "deadline": "See hackathon page for deadlines",
            "location": "Online / Global",
            "eligibility": "Open to all students and developers",
        })
        
    logger.info(f"Collected {len(items)} hackathon items from {source_name}")
    return items

def fetch_hacker_news_api(limit: int = config.MAX_ITEMS_PER_FEED) -> List[Dict[str, Any]]:
    """
    Fetches top stories using Hacker News official Firebase REST API.
    Provides real story points, author, and comments count.
    """
    api_base = getattr(config, "HN_API_BASE", "https://hacker-news.firebaseio.com/v0")
    top_url = f"{api_base}/topstories.json"
    data = fetch_data(top_url, timeout=min(config.HTTP_TIMEOUT, 6))
    if not data:
        return []
    try:
        story_ids = json.loads(data.decode("utf-8"))
        if not isinstance(story_ids, list):
            return []
    except Exception as e:
        logger.warning(f"Error parsing HN topstories JSON: {e}")
        return []

    items = []
    for sid in story_ids[:limit]:
        item_url = f"{api_base}/item/{sid}.json"
        item_data = fetch_data(item_url, timeout=min(config.HTTP_TIMEOUT, 6))
        if not item_data:
            continue
        try:
            story = json.loads(item_data.decode("utf-8"))
            if not isinstance(story, dict) or story.get("type") != "story":
                continue
            title = str(story.get("title", "")).strip()
            link = str(story.get("url") or f"https://news.ycombinator.com/item?id={sid}").strip()
            score = story.get("score", 0)
            by = story.get("by", "community")
            descendants = story.get("descendants", 0)
            summary = f"Trending on Hacker News ({score} points, {descendants} comments) posted by @{by}."
            
            time_val = story.get("time")
            date_str = datetime.fromtimestamp(time_val).strftime("%Y-%m-%d %H:%M:%S") if time_val else ""
            
            if title and link:
                items.append({
                    "source": "Hacker News API",
                    "category": "news",
                    "title": title,
                    "url": link,
                    "summary": summary,
                    "date_str": date_str,
                    "deadline": "Ongoing / Published",
                    "location": "Global",
                    "eligibility": "Open to all",
                })
        except Exception as e:
            logger.warning(f"Error parsing HN story {sid}: {e}")
            continue
            
    return items

def fetch_hacker_news(limit: int = config.MAX_ITEMS_PER_FEED) -> List[Dict[str, Any]]:
    """
    Collects tech news & updates from Hacker News API, with fallback to HN RSS and TechCrunch.
    """
    # 1. Try official Firebase REST API first (fast, reliable, rich metadata)
    try:
        api_items = fetch_hacker_news_api(limit=limit)
        if api_items:
            logger.info(f"Collected {len(api_items)} tech news items from Hacker News API")
            return api_items
    except Exception as e:
        logger.warning(f"Hacker News API attempt failed: {e}")

    # 2. Fallback to Hacker News RSS
    feed_cfg = config.FEEDS.get("news", {})
    primary_url = feed_cfg.get("primary", "https://news.ycombinator.com/rss")
    fallback_url = feed_cfg.get("fallback", "https://techcrunch.com/feed/")
    
    data = fetch_data(primary_url)
    source_name = "Hacker News RSS"
    
    if not data and fallback_url:
        logger.info(f"HN RSS primary returned no data. Falling back to {fallback_url}")
        data = fetch_data(fallback_url)
        source_name = "TechCrunch"
        
    entries = parse_xml_feed(data) if data else []
    items = []
    for entry in entries[:limit]:
        title = entry["title"]
        url = entry["link"]
        raw_summary = entry["summary"]
        
        # Clean out HN RSS "Comments" artifact or empty placeholder
        if not raw_summary or raw_summary.lower().strip() in ("comments", "untitled"):
            summary = f"Trending tech development and industry discussion on {source_name}: {title}."
        else:
            summary = raw_summary
            
        items.append({
            "source": source_name,
            "category": "news",
            "title": title,
            "url": url,
            "summary": summary,
            "date_str": entry.get("date_str", ""),
            "deadline": "Ongoing / Published",
            "location": "Global",
            "eligibility": "Open to all",
        })
        
    logger.info(f"Collected {len(items)} tech news items from {source_name}")
    return items

def fetch_reddit_courses(limit: int = config.MAX_ITEMS_PER_FEED) -> List[Dict[str, Any]]:
    """
    Collects learning resources & courses from Reddit r/learnmachinelearning (fallback FreeCodeCamp).
    """
    feed_cfg = config.FEEDS.get("courses", {})
    primary_url = feed_cfg.get("primary", "https://www.reddit.com/r/learnmachinelearning/hot.rss")
    fallback_url = feed_cfg.get("fallback", "https://www.freecodecamp.org/news/rss/")
    
    data = fetch_data(primary_url)
    source_name = "Reddit r/learnmachinelearning"
    
    if not data and fallback_url:
        logger.info(f"Reddit primary returned no data. Falling back to {fallback_url}")
        data = fetch_data(fallback_url)
        source_name = "FreeCodeCamp News"
        
    entries = parse_xml_feed(data) if data else []
    items = []
    for entry in entries[:limit]:
        title = entry["title"]
        url = entry["link"]
        summary = entry["summary"] or f"Machine learning resource and tutorial from {source_name}."
        
        items.append({
            "source": source_name,
            "category": "course",
            "title": title,
            "url": url,
            "summary": summary,
            "date_str": entry.get("date_str", ""),
            "deadline": "Self-paced / Always open",
            "location": "Online",
            "eligibility": "Free / Open to all learners",
        })
        
    logger.info(f"Collected {len(items)} course items from {source_name}")
    return items

def fetch_weworkremotely_jobs(limit: int = config.MAX_ITEMS_PER_FEED) -> List[Dict[str, Any]]:
    """
    Collects remote developer jobs from WeWorkRemotely RSS.
    """
    feed_cfg = config.FEEDS.get("jobs_wwr", {})
    url = feed_cfg.get("primary", "https://weworkremotely.com/categories/remote-programming-jobs.rss")
    
    data = fetch_data(url)
    entries = parse_xml_feed(data) if data else []
    items = []
    
    for entry in entries[:limit]:
        title = entry["title"]
        url_link = entry["link"]
        summary = entry["summary"]
        
        company = "Tech Company"
        role = title
        if ":" in title:
            parts = title.split(":", 1)
            company = parts[0].strip()
            role = parts[1].strip()
            
        items.append({
            "source": "WeWorkRemotely",
            "category": "job",
            "title": f"{role} at {company}",
            "role": role,
            "company": company,
            "url": url_link,
            "summary": summary,
            "experience": "Entry / Mid / Senior",
            "location": "Remote",
            "skills": "Software Development / Engineering",
            "date_str": entry.get("date_str", ""),
            "deadline": "Until filled",
            "eligibility": "Open to remote applicants",
        })
        
    logger.info(f"Collected {len(items)} job items from WeWorkRemotely")
    return items

def fetch_remoteok_jobs(limit: int = config.MAX_ITEMS_PER_FEED) -> List[Dict[str, Any]]:
    """
    Collects tech jobs from RemoteOK public JSON API (with fallback to RSS).
    """
    feed_cfg = config.FEEDS.get("jobs_remoteok", {})
    url = feed_cfg.get("primary", "https://remoteok.com/api")
    fallback_url = feed_cfg.get("fallback", "https://remoteok.io/remote-jobs.rss")
    
    data = fetch_data(url)
    items = []
    if data:
        try:
            parsed = json.loads(data.decode("utf-8", errors="ignore"))
            if isinstance(parsed, list):
                for job in parsed:
                    if len(items) >= limit:
                        break
                    if not isinstance(job, dict) or "position" not in job:
                        continue
                    
                    role = str(job.get("position") or "Software Engineer").strip()
                    company = str(job.get("company") or "Tech Company").strip()
                    raw_job_url = job.get("url")
                    if not raw_job_url:
                        continue
                    job_url = str(raw_job_url).strip()
                    if job_url.startswith("/"):
                        job_url = "https://remoteok.com" + job_url
                    elif not job_url.startswith("http"):
                        job_url = "https://remoteok.com/" + job_url
                        
                    raw_tags = job.get("tags")
                    tags = [str(t) for t in raw_tags if t] if isinstance(raw_tags, list) else []
                    skills = ", ".join(tags[:4]) if tags else "Tech / Software"
                    desc = strip_html_tags(str(job.get("description") or ""))
                    loc = str(job.get("location") or "Remote")
                    
                    items.append({
                        "source": "RemoteOK",
                        "category": "job",
                        "title": f"{role} at {company}",
                        "role": role,
                        "company": company,
                        "url": job_url,
                        "summary": desc,
                        "experience": "Open / Remote",
                        "location": loc,
                        "skills": skills,
                        "date_str": str(job.get("date") or ""),
                        "deadline": "Until filled",
                        "eligibility": "Open to remote candidates",
                    })
        except Exception as e:
            logger.warning(f"Error parsing RemoteOK JSON: {e}")

    # Fallback to RSS feed if API returned no data
    if not items and fallback_url:
        logger.info(f"RemoteOK API returned 0 items. Trying fallback: {fallback_url}")
        rss_data = fetch_data(fallback_url)
        if rss_data:
            entries = parse_xml_feed(rss_data)
            for entry in entries[:limit]:
                title = entry["title"]
                items.append({
                    "source": "RemoteOK RSS",
                    "category": "job",
                    "title": title,
                    "role": title,
                    "company": "Remote Company",
                    "url": entry["link"],
                    "summary": entry["summary"],
                    "experience": "Remote",
                    "location": "Remote",
                    "skills": "Software Development",
                    "date_str": entry.get("date_str", ""),
                    "deadline": "Until filled",
                    "eligibility": "Open to remote candidates",
                })
            
    logger.info(f"Collected {len(items)} job items from RemoteOK")
    return items

def fetch_tech_events(limit: int = config.MAX_ITEMS_PER_FEED) -> List[Dict[str, Any]]:
    """
    Collects upcoming tech events, conferences, and workshops from Dev.to events feed.
    """
    feed_cfg = config.FEEDS.get("events", {})
    url = feed_cfg.get("primary", "https://dev.to/feed/tag/events")
    
    data = fetch_data(url)
    entries = parse_xml_feed(data) if data else []
    items = []
    
    for entry in entries[:limit]:
        title = entry["title"]
        url_link = entry["link"]
        summary = entry["summary"] or "Tech workshop and community event."
        
        items.append({
            "source": "Dev Events & Workshops",
            "category": "event",
            "title": title,
            "url": url_link,
            "summary": summary,
            "date_str": entry.get("date_str", ""),
            "deadline": "See event details",
            "location": "Online / Global",
            "eligibility": "Developers, students, and tech enthusiasts",
        })
        
    logger.info(f"Collected {len(items)} event items from Dev Events")
    return items

CURATED_SEED_ITEMS: List[Dict[str, str]] = [
    {
        "id": "s1",
        "type": "hackathon",
        "title": "HackGlobal by Polaris School of Technology",
        "sum": "A global online hackathon with builders from five countries. Register on HackCulture.",
        "date": "2026-11-14",
        "where": "Online",
        "src": "HackCulture",
        "link": "https://hackculture.io",
        "posted": "2026-11-06"
    },
    {
        "id": "s2",
        "type": "hackathon",
        "title": "Gemma 4 Developer Agent Paper Track",
        "sum": "Submit a research paper that advances agentic software engineering.",
        "date": "",
        "where": "Online",
        "src": "Kaggle",
        "link": "https://www.kaggle.com/competitions/gemma-4-developer-agent-paper",
        "posted": "2026-11-06"
    },
    {
        "id": "s3",
        "type": "hackathon",
        "title": "Codegeist 2026: Build with context",
        "sum": "Build on Forge and extend Atlassian products. Prize pool of $120K.",
        "date": "",
        "where": "Online",
        "src": "Devpost",
        "link": "https://codegeist2026.devpost.com",
        "posted": "2026-11-06"
    },
    {
        "id": "s4",
        "type": "hackathon",
        "title": "Meta VR Start Developer Competition 2026",
        "sum": "Build VR apps with Meta VR Start. Cash prizes total $1M.",
        "date": "",
        "where": "Online",
        "src": "Devpost",
        "link": "https://devpost.com",
        "posted": "2026-11-06"
    },
    {
        "id": "s5",
        "type": "hackathon",
        "title": "AWS MSP AI Hackathon",
        "sum": "Build the AI-first MSP. Run smarter and deliver more.",
        "date": "",
        "where": "Online",
        "src": "Devpost",
        "link": "https://share.google/YUIvbsyKn93aeDHFG",
        "posted": "2026-11-06"
    },
    {
        "id": "s6",
        "type": "hackathon",
        "title": "HackStack: India’s Online Innovation Sprint",
        "sum": "A fully online innovation competition open to all participants in India.",
        "date": "",
        "where": "Online",
        "src": "Luma",
        "link": "https://luma.com",
        "posted": "2026-11-06"
    },
    {
        "id": "s7",
        "type": "event",
        "title": "QonfX Hyderabad 2026: AI, Quality & Tech Leadership",
        "sum": "Invite-only conference on AI-native engineering, QA, and leadership. Exact venue shared after confirmation.",
        "date": "2026-12-03",
        "where": "HITEC City, Hyderabad",
        "src": "Luma",
        "link": "https://luma.com",
        "posted": "2026-11-06"
    },
    {
        "id": "s8",
        "type": "course",
        "title": "DataCamp Free Access Week",
        "sum": "Free access to 750+ data and AI courses, career tracks, and certifications. No credit card needed.",
        "date": "2026-11-08",
        "where": "Online",
        "src": "DataCamp",
        "link": "https://www.datacamp.com",
        "posted": "2026-11-05"
    },
    {
        "id": "s9",
        "type": "job",
        "title": "Walk-in drive at Ksquare Group: Salesforce and Data Engineering",
        "sum": "Five roles: Salesforce Developer, Salesforce QE, Marketing Cloud Developer, Data Engineer (GCP), Data Engineer (AWS). 10:30 AM to 4:00 PM.",
        "date": "2026-11-14",
        "where": "Nanakramguda, Hyderabad",
        "src": "Community admin",
        "link": "",
        "posted": "2026-11-07"
    },
    {
        "id": "s10",
        "type": "job",
        "title": "Software Engineer (C#, .NET, WPF) at Medtronic",
        "sum": "Build Windows desktop applications. Open to engineers in Hyderabad.",
        "date": "",
        "where": "Hyderabad",
        "src": "LinkedIn",
        "link": "https://lnkd.in/p/dG8pVZs7",
        "posted": "2026-11-07"
    },
    {
        "id": "s11",
        "type": "job",
        "title": "EIR Intern, Agentic AI at Prozo",
        "sum": "Work in the Founder’s Office. Build AI-powered apps and automations with LLMs and agentic AI.",
        "date": "",
        "where": "Not stated",
        "src": "Community admin",
        "link": "https://docs.google.com/forms",
        "posted": "2026-11-07"
    },
    {
        "id": "s12",
        "type": "job",
        "title": "LLM Fine-Tuning Engineer",
        "sum": "2 to 5 years of experience. B.E. or B.Tech in CS. Work from office.",
        "date": "",
        "where": "Bangalore",
        "src": "Community admin",
        "link": "",
        "posted": "2026-11-07"
    }
]

def normalize_feed_item(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Normalizes raw dictionary from Legion Feed into standard pipeline format."""
    category = raw.get("type", "news").lower().strip()
    if category not in ("hackathon", "job", "event", "course", "news"):
        category = "news"
        
    title = str(raw.get("title", "Untitled")).strip()
    feed_id = str(raw.get("id", "")).strip()
    raw_link = str(raw.get("link", "")).strip()
    
    # If link is missing, construct a canonical URL using feed ID or title slug
    if raw_link:
        url = raw_link
    elif feed_id:
        url = f"https://legion.community/feed/{feed_id}"
    else:
        clean_slug = re.sub(r'[^a-zA-Z0-9]+', '-', title.lower()).strip('-')
        url = f"https://legion.community/feed/{clean_slug}"
        
    summary = str(raw.get("sum", "")).strip()
    date_str = str(raw.get("date", "")).strip()
    location = str(raw.get("where", "")).strip() or "Online"
    source = str(raw.get("src", "")).strip() or "Legion Community Feed"
    
    norm_item: Dict[str, Any] = {
        "source": source,
        "category": category,
        "title": title,
        "url": url,
        "summary": summary or f"{title} from {source}",
        "date_str": date_str,
        "deadline": date_str if date_str else "See announcement",
        "location": location,
        "eligibility": "Open to all",
    }
    
    if category == "job":
        company = "Tech Company"
        role = title
        if " at " in title:
            parts = title.split(" at ", 1)
            role = parts[0].strip()
            company = parts[1].strip()
        elif ":" in title:
            parts = title.split(":", 1)
            company = parts[0].strip()
            role = parts[1].strip()
        elif source and source.lower() not in ("community admin", "admin"):
            company = source
        
        norm_item["company"] = company
        norm_item["role"] = role
        norm_item["location"] = location or "Remote / In-office"
        norm_item["experience"] = "Open / See post"
        norm_item["skills"] = "Tech / Engineering"
        
    return norm_item

def parse_legion_feed_html(html_text: str) -> List[Dict[str, Any]]:
    """
    Parses Legion Feed HTML dashboard content to extract opportunity items.
    Extracts all fields: id, type, title, sum, date, where, src, link, posted.
    """
    if not html_text:
        return []
    m = re.search(r'var\s+SEED\s*=\s*\[([\s\S]*?)\]\s*;', html_text)
    if not m:
        return []
    seed_text = m.group(1)
    obj_re = re.compile(r'\{([^{}]+)\}')
    raw_items = []
    for obj_match in obj_re.finditer(seed_text):
        body = obj_match.group(1)
        pairs = re.findall(r'(\w+)\s*:\s*"((?:\\\"|[^"])*)"', body)
        item_dict = {k: v.replace('\\"', '"') for k, v in pairs}
        if item_dict and item_dict.get("title"):
            raw_items.append(item_dict)

    return [normalize_feed_item(raw) for raw in raw_items]

def fetch_legion_feed_items(
    html_path: Optional[str] = None,
    limit: int = config.MAX_ITEMS_PER_FEED
) -> List[Dict[str, Any]]:
    """
    Reads a Legion Feed HTML file and returns normalized opportunity items.
    Checks html_path, config.LEGION_FEED_HTML_PATH, and Downloads folder.
    Falls back to CURATED_SEED_ITEMS if file is not found.
    """
    import os
    candidates = []
    if html_path:
        candidates.append(html_path)
    if hasattr(config, "LEGION_FEED_HTML_PATH"):
        candidates.append(config.LEGION_FEED_HTML_PATH)
    downloads_path = os.path.join(os.path.expanduser("~"), "Downloads", "Legion Feed.html")
    candidates.append(downloads_path)
    
    for path in candidates:
        if path and os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
                items = parse_legion_feed_html(content)
                if items:
                    logger.info(f"Loaded {len(items)} items from Legion Feed HTML ({path})")
                    return items[:limit]
            except Exception as e:
                logger.warning(f"Error reading Legion Feed HTML from {path}: {e}")
                
    logger.info(f"Using curated default seed items ({len(CURATED_SEED_ITEMS)} available).")
    return [normalize_feed_item(it) for it in CURATED_SEED_ITEMS][:limit]



def fetch_commudle_events(limit: int = config.MAX_ITEMS_PER_FEED) -> List[Dict[str, Any]]:
    """
    Collects offline tech meetups and events in Hyderabad from Commudle API.
    Targets key local tech communities (GDG Hyderabad, HydPy, React Hyderabad, AWS User Group Hyderabad, AI Community Hyderabad).
    """
    url = getattr(config, "COMMUDLE_HYDERABAD_URL", "https://api.commudle.com/api/v1/events?city=Hyderabad")
    data = fetch_data(url)
    items: List[Dict[str, Any]] = []

    if data:
        try:
            parsed = json.loads(data.decode("utf-8", errors="ignore"))
            raw_events = []
            if isinstance(parsed, dict):
                raw_events = parsed.get("data") or parsed.get("events") or parsed.get("result") or []
            elif isinstance(parsed, list):
                raw_events = parsed

            for ev in raw_events:
                if len(items) >= limit:
                    break
                if not isinstance(ev, dict):
                    continue

                title = str(ev.get("title") or ev.get("name") or "Community Meetup").strip()
                raw_url = ev.get("url") or ev.get("event_url") or ""
                slug = ev.get("slug") or ev.get("id") or ""
                if raw_url and str(raw_url).startswith("http"):
                    event_url = str(raw_url).strip()
                elif slug:
                    event_url = f"https://www.commudle.com/events/{slug}"
                else:
                    continue

                summary = strip_html_tags(str(ev.get("description") or ev.get("about") or ev.get("summary") or ""))
                if not summary:
                    summary = f"Developer meetup and workshop hosted on Commudle: {title}."

                date_val = str(ev.get("start_date") or ev.get("date") or ev.get("starts_at") or "").strip()
                venue_obj = ev.get("venue")
                venue_name = ""
                if isinstance(venue_obj, dict):
                    venue_name = str(venue_obj.get("name") or venue_obj.get("address") or venue_obj.get("city") or "").strip()
                elif isinstance(venue_obj, str):
                    venue_name = venue_obj.strip()

                comm_obj = ev.get("community")
                comm_name = ""
                if isinstance(comm_obj, dict):
                    comm_name = str(comm_obj.get("name") or "").strip()
                elif isinstance(comm_obj, str):
                    comm_name = comm_obj.strip()

                # Track key Hyderabad communities specifically
                matched_comm = comm_name
                combined_desc = f"{title} {comm_name} {summary}"
                for target_comm in getattr(config, "HYDERABAD_COMMUNITIES", []):
                    if target_comm.lower() in combined_desc.lower():
                        matched_comm = target_comm
                        break

                loc = f"Hyderabad ({venue_name})" if venue_name and "hyderabad" not in venue_name.lower() else (venue_name or "Hyderabad")
                source_label = f"Commudle ({matched_comm})" if matched_comm else "Commudle Hyderabad"

                items.append({
                    "source": source_label,
                    "category": "event",
                    "title": title,
                    "url": event_url,
                    "summary": summary,
                    "date_str": date_val,
                    "deadline": date_val or "RSVP on Commudle",
                    "location": loc,
                    "eligibility": "Open to developers and tech community",
                })
        except Exception as e:
            logger.warning(f"Error parsing Commudle events: {e}")

    # Fallback to HTML parsing if JSON API returns no items
    if not items:
        fallback_url = getattr(config, "COMMUDLE_FALLBACK_URL", config.FEEDS.get("commudle_hyderabad", {}).get("fallback"))
        if fallback_url:
            fallback_data = fetch_data(fallback_url)
            if fallback_data:
                try:
                    html_text = fallback_data.decode("utf-8", errors="ignore")
                    matches = re.findall(
                        r'<a[^>]+href="([^"]*/events/[^"]+)"[^>]*>[\s\S]*?<h[2-5][^>]*>([^<]+)</h[2-5]>',
                        html_text
                    )
                    for href, h_title in matches:
                        if len(items) >= limit:
                            break
                        clean_title = " ".join(h_title.split())
                        full_url = href if href.startswith("http") else f"https://www.commudle.com{href}"
                        items.append({
                            "source": "Commudle Hyderabad",
                            "category": "event",
                            "title": clean_title,
                            "url": full_url,
                            "summary": f"Community tech event in Hyderabad: {clean_title}.",
                            "date_str": "",
                            "deadline": "RSVP on Commudle",
                            "location": "Hyderabad",
                            "eligibility": "Developers and tech community",
                        })
                except Exception as e:
                    logger.warning(f"Error parsing Commudle fallback HTML: {e}")

    logger.info(f"Collected {len(items)} items from Commudle Hyderabad")
    return items

def fetch_luma_hyderabad_events(limit: int = config.MAX_ITEMS_PER_FEED) -> List[Dict[str, Any]]:
    """
    Collects Hyderabad tech & AI meetups from Luma location feeds and community calendars.
    """
    primary_url = getattr(config, "LUMA_HYDERABAD_URL", "https://api.lu.ma/discover/events?location=Hyderabad")
    fallback_url = getattr(config, "LUMA_INDIA_FEED", "https://luma.com/MeetUpsinIndia")
    
    data = fetch_data(primary_url)
    items: List[Dict[str, Any]] = []

    if data:
        try:
            parsed = json.loads(data.decode("utf-8", errors="ignore"))
            raw_list = []
            if isinstance(parsed, dict):
                raw_list = parsed.get("events") or parsed.get("entries") or parsed.get("data") or []
            elif isinstance(parsed, list):
                raw_list = parsed

            for ev in raw_list:
                if len(items) >= limit:
                    break
                if not isinstance(ev, dict):
                    continue

                event_data = ev.get("event") if isinstance(ev.get("event"), dict) else ev
                title = str(event_data.get("name") or event_data.get("title") or "Luma Meetup").strip()
                url_slug = event_data.get("url") or event_data.get("url_slug") or event_data.get("id") or ""
                if str(url_slug).startswith("http"):
                    event_url = str(url_slug).strip()
                elif url_slug:
                    event_url = f"https://lu.ma/{url_slug}"
                else:
                    continue

                summary = strip_html_tags(str(event_data.get("description") or event_data.get("summary") or ""))
                if not summary:
                    summary = f"Tech community event in Hyderabad: {title}."

                date_val = str(event_data.get("start_at") or event_data.get("date") or "").strip()
                geo = str(event_data.get("geo_address_info") or event_data.get("location") or "Hyderabad").strip()
                loc = geo if "hyderabad" in geo.lower() else f"Hyderabad ({geo})"

                items.append({
                    "source": "Luma Hyderabad",
                    "category": "event",
                    "title": title,
                    "url": event_url,
                    "summary": summary,
                    "date_str": date_val,
                    "deadline": date_val or "RSVP on Luma",
                    "location": loc,
                    "eligibility": "Tech builders, founders, and engineers",
                })
        except Exception as e:
            logger.warning(f"Error parsing Luma JSON: {e}")

    # Fallback to scraping calendar / community page HTML if JSON returned no items
    if not items and fallback_url:
        fallback_data = fetch_data(fallback_url)
        if fallback_data:
            try:
                html_text = fallback_data.decode("utf-8", errors="ignore")
                # 1. Look for embedded Next.js JSON __NEXT_DATA__
                m = re.search(r'<script id="__NEXT_DATA__"[^>]*>([\s\S]*?)</script>', html_text)
                if m:
                    next_data = json.loads(m.group(1))
                    page_props = next_data.get("props", {}).get("pageProps", {})
                    candidate_events = page_props.get("events") or page_props.get("initialEvents") or []
                    for ev in candidate_events:
                        if len(items) >= limit:
                            break
                        if not isinstance(ev, dict):
                            continue
                        e_obj = ev.get("event") if isinstance(ev.get("event"), dict) else ev
                        title = str(e_obj.get("name") or "Luma Meetup").strip()
                        url_slug = e_obj.get("url") or e_obj.get("url_slug") or ""
                        event_url = url_slug if url_slug.startswith("http") else f"https://lu.ma/{url_slug}"
                        summary = strip_html_tags(str(e_obj.get("description") or ""))
                        loc = str(e_obj.get("location") or "Hyderabad")
                        if "hyderabad" in (title + summary + loc).lower():
                            items.append({
                                "source": "Luma Hyderabad",
                                "category": "event",
                                "title": title,
                                "url": event_url,
                                "summary": summary or f"Tech event in Hyderabad: {title}",
                                "date_str": str(e_obj.get("start_at") or ""),
                                "deadline": "RSVP on Luma",
                                "location": loc,
                                "eligibility": "Open to all",
                            })

                # 2. Look for JSON-LD in Luma page
                if not items:
                    json_ld_blocks = re.findall(r'<script type="application/ld\+json">([\s\S]*?)</script>', html_text)
                    for block in json_ld_blocks:
                        try:
                            parsed_ld = json.loads(block)
                            candidates = []
                            if isinstance(parsed_ld, list):
                                candidates = parsed_ld
                            elif isinstance(parsed_ld, dict):
                                if "@graph" in parsed_ld and isinstance(parsed_ld["@graph"], list):
                                    candidates = parsed_ld["@graph"]
                                else:
                                    candidates = [parsed_ld]
                            for c in candidates:
                                if len(items) >= limit:
                                    break
                                if not isinstance(c, dict) or str(c.get("@type", "")).lower() != "event":
                                    continue
                                t_val = str(c.get("name") or "").strip()
                                u_val = str(c.get("url") or "").strip()
                                s_val = strip_html_tags(str(c.get("description") or ""))
                                d_val = str(c.get("startDate") or "").strip()
                                l_val = str(c.get("location", {}).get("name") if isinstance(c.get("location"), dict) else c.get("location") or "Hyderabad").strip()
                                if "hyderabad" in (t_val + s_val + l_val).lower() and u_val:
                                    items.append({
                                        "source": "Luma Hyderabad",
                                        "category": "event",
                                        "title": t_val,
                                        "url": u_val if u_val.startswith("http") else f"https://lu.ma/{u_val}",
                                        "summary": s_val or f"Tech meetup in Hyderabad: {t_val}",
                                        "date_str": d_val,
                                        "deadline": d_val or "RSVP on Luma",
                                        "location": l_val if "hyderabad" in l_val.lower() else f"Hyderabad ({l_val})",
                                        "eligibility": "Tech builders and developers",
                                    })
                        except Exception:
                            continue

                # 3. HTML anchor tag regex scraper fallback
                if not items:
                    matches = re.findall(
                        r'<a[^>]+href="([^"]*/[a-zA-Z0-9_\-]+)"[^>]*>[\s\S]*?<h[1-6][^>]*>([^<]+)</h[1-6]>',
                        html_text
                    )
                    for href, h_title in matches:
                        if len(items) >= limit:
                            break
                        clean_title = " ".join(h_title.split())
                        if "hyderabad" in clean_title.lower() or "hyd" in clean_title.lower():
                            full_url = href if href.startswith("http") else f"https://lu.ma{href}"
                            items.append({
                                "source": "Luma Hyderabad",
                                "category": "event",
                                "title": clean_title,
                                "url": full_url,
                                "summary": f"Community meetup in Hyderabad: {clean_title}.",
                                "date_str": "",
                                "deadline": "RSVP on Luma",
                                "location": "Hyderabad",
                                "eligibility": "Tech builders, founders, and engineers",
                            })
            except Exception as e:
                logger.warning(f"Error parsing Luma fallback HTML: {e}")

    logger.info(f"Collected {len(items)} items from Luma Hyderabad")
    return items

def fetch_meetup_hyderabad_events(limit: int = config.MAX_ITEMS_PER_FEED) -> List[Dict[str, Any]]:
    """
    Collects upcoming technology, machine learning, and startup meetups in Hyderabad via Meetup.com.
    """
    url = getattr(config, "MEETUP_HYDERABAD_URL", "https://www.meetup.com/find/?location=in--Hyderabad&radius=25&source=EVENTS&categoryFilter=technology")
    data = fetch_data(url)
    items: List[Dict[str, Any]] = []
    if not data:
        return items

    try:
        # 0. Check for RSS / Atom XML feed
        if b"<rss" in data or b"<feed" in data:
            entries = parse_xml_feed(data)
            for entry in entries[:limit]:
                items.append({
                    "source": "Meetup Hyderabad",
                    "category": "event",
                    "title": entry["title"],
                    "url": entry["link"],
                    "summary": entry["summary"] or f"Tech meetup in Hyderabad: {entry['title']}.",
                    "date_str": entry.get("date_str", ""),
                    "deadline": "RSVP on Meetup",
                    "location": "Hyderabad",
                    "eligibility": "Developers, learners, and technologists",
                })
            logger.info(f"Collected {len(items)} items from Meetup RSS/Atom feed")
            return items

        content = data.decode("utf-8", errors="ignore")
        # 1. Check for JSON-LD schema objects (supporting single objects, arrays, and @graph)
        json_ld_blocks = re.findall(r'<script type="application/ld\+json">([\s\S]*?)</script>', content)
        for block in json_ld_blocks:
            try:
                parsed_ld = json.loads(block)
                candidates = []
                if isinstance(parsed_ld, list):
                    candidates = parsed_ld
                elif isinstance(parsed_ld, dict):
                    if "@graph" in parsed_ld and isinstance(parsed_ld["@graph"], list):
                        candidates = parsed_ld["@graph"]
                    else:
                        candidates = [parsed_ld]

                for c in candidates:
                    if len(items) >= limit:
                        break
                    if not isinstance(c, dict):
                        continue
                    c_type = c.get("@type")
                    is_event = False
                    if isinstance(c_type, str) and c_type.lower() in ("event", "http://schema.org/event", "https://schema.org/event"):
                        is_event = True
                    elif isinstance(c_type, list) and any(isinstance(t, str) and t.lower() == "event" for t in c_type):
                        is_event = True
                    if not is_event:
                        continue

                    title = str(c.get("name") or "Meetup Event").strip()
                    event_url = str(c.get("url") or "").strip()
                    if not event_url:
                        continue

                    summary = strip_html_tags(str(c.get("description") or ""))
                    date_val = str(c.get("startDate") or "").strip()
                    loc_val = "Hyderabad"
                    loc_obj = c.get("location")
                    if isinstance(loc_obj, dict):
                        loc_val = str(loc_obj.get("name") or loc_obj.get("address") or "Hyderabad").strip()

                    items.append({
                        "source": "Meetup Hyderabad",
                        "category": "event",
                        "title": title,
                        "url": event_url,
                        "summary": summary or f"Tech meetup in Hyderabad: {title}",
                        "date_str": date_val,
                        "deadline": date_val or "RSVP on Meetup",
                        "location": loc_val if "hyderabad" in loc_val.lower() else f"Hyderabad ({loc_val})",
                        "eligibility": "Developers, learners, and technologists",
                    })
            except Exception:
                continue

        # 2. If no JSON-LD found, parse Next.js __NEXT_DATA__
        if not items:
            m = re.search(r'<script id="__NEXT_DATA__"[^>]*>([\s\S]*?)</script>', content)
            if m:
                next_obj = json.loads(m.group(1))
                apollo_state = next_obj.get("props", {}).get("pageProps", {}).get("__APOLLO_STATE__", {})
                for k, v in apollo_state.items():
                    if len(items) >= limit:
                        break
                    if isinstance(v, dict) and v.get("__typename") == "Event":
                        title = str(v.get("title") or "").strip()
                        url_path = str(v.get("eventUrl") or "").strip()
                        if not title or not url_path:
                            continue
                        event_url = url_path if url_path.startswith("http") else f"https://www.meetup.com{url_path}"
                        items.append({
                            "source": "Meetup Hyderabad",
                            "category": "event",
                            "title": title,
                            "url": event_url,
                            "summary": f"Upcoming tech meetup in Hyderabad: {title}.",
                            "date_str": str(v.get("dateTime") or ""),
                            "deadline": "RSVP on Meetup",
                            "location": "Hyderabad",
                            "eligibility": "Tech enthusiasts and developers",
                        })

        # 3. HTML regex scraper fallback
        if not items:
            matches = re.findall(
                r'<a[^>]+href="([^"]*/events/\d+[^"]*)"[^>]*>[\s\S]*?<h[2-5][^>]*>([^<]+)</h[2-5]>',
                content
            )
            for href, h_title in matches[:limit]:
                clean_title = " ".join(h_title.split())
                full_url = href if href.startswith("http") else f"https://www.meetup.com{href}"
                items.append({
                    "source": "Meetup Hyderabad",
                    "category": "event",
                    "title": clean_title,
                    "url": full_url,
                    "summary": f"Upcoming tech meetup in Hyderabad: {clean_title}.",
                    "date_str": "",
                    "deadline": "RSVP on Meetup",
                    "location": "Hyderabad",
                    "eligibility": "Developers and technologists",
                })
    except Exception as e:
        logger.warning(f"Error parsing Meetup Hyderabad: {e}")

    logger.info(f"Collected {len(items)} items from Meetup Hyderabad")
    return items

def fetch_unstop_hackathons(limit: int = config.MAX_ITEMS_PER_FEED) -> List[Dict[str, Any]]:
    """
    Collects hackathons and developer competitions from Unstop (India/Hyderabad ecosystem).
    """
    url = getattr(config, "UNSTOP_HACKATHONS_URL", "https://unstop.com/api/public/opportunity/search-result?opportunity=hackathons&quickApply=true&locations=Hyderabad")
    data = fetch_data(url)
    items: List[Dict[str, Any]] = []
    if not data:
        return items

    try:
        parsed = json.loads(data.decode("utf-8", errors="ignore"))
        raw_list = []
        if isinstance(parsed, dict):
            raw_data = parsed.get("data")
            if isinstance(raw_data, dict):
                raw_list = raw_data.get("data") or []
            elif isinstance(raw_data, list):
                raw_list = raw_data
        elif isinstance(parsed, list):
            raw_list = parsed

        for op in raw_list:
            if len(items) >= limit:
                break
            if not isinstance(op, dict):
                continue

            title = str(op.get("title") or "Hackathon on Unstop").strip()
            slug = op.get("public_url") or op.get("slug") or ""
            if str(slug).startswith("http"):
                event_url = str(slug).strip()
            elif slug:
                event_url = f"https://unstop.com/hackathons/{slug}"
            else:
                continue

            summary = strip_html_tags(str(op.get("details") or op.get("banner_alt") or ""))
            if not summary:
                org_name = op.get("organisation", {}).get("name") if isinstance(op.get("organisation"), dict) else ""
                summary = f"Developer competition on Unstop organized by {org_name or 'Unstop partner'}."

            deadline = str(op.get("end_reg_date") or op.get("reg_end_date") or "See competition page").strip()
            start_date = str(op.get("start_date") or "").strip()
            region = str(op.get("region") or op.get("locations") or "India / Online").strip()

            items.append({
                "source": "Unstop Hackathons",
                "category": "hackathon",
                "title": title,
                "url": event_url,
                "summary": summary,
                "date_str": start_date,
                "deadline": deadline,
                "location": region,
                "eligibility": "College students and developers across India",
            })
    except Exception as e:
        logger.warning(f"Error parsing Unstop hackathons: {e}")

    logger.info(f"Collected {len(items)} hackathon items from Unstop")
    return items

def fetch_devpost_hyderabad_hackathons(limit: int = config.MAX_ITEMS_PER_FEED) -> List[Dict[str, Any]]:
    """
    Collects Devpost hackathons targeting Hyderabad hubs, institutes (IIIT-H, T-Hub, CBIT), and India.
    """
    url = getattr(config, "DEVPOST_HYDERABAD_URL", "https://devpost.com/hackathons?search=Hyderabad")
    data = fetch_data(url)
    items: List[Dict[str, Any]] = []
    if not data:
        return items

    try:
        # Check if response is RSS XML
        if b"<rss" in data or b"<feed" in data:
            entries = parse_xml_feed(data)
            for entry in entries[:limit]:
                items.append({
                    "source": "Devpost Hyderabad",
                    "category": "hackathon",
                    "title": entry["title"],
                    "url": entry["link"],
                    "summary": entry["summary"] or f"Hyderabad hackathon: {entry['title']}.",
                    "date_str": entry.get("date_str", ""),
                    "deadline": "See Devpost hackathon page",
                    "location": "Hyderabad / Online",
                    "eligibility": "Open to all students and developers",
                })
        else:
            # HTML scraping for Devpost search results
            html_text = data.decode("utf-8", errors="ignore")
            matches = re.findall(
                r'<a[^>]+class="[^"]*clearfix[^"]*"[^>]+href="([^"]+)"[^>]*>[\s\S]*?<h5>([^<]+)</h5>',
                html_text
            )
            for href, h_title in matches[:limit]:
                clean_title = " ".join(h_title.split())
                full_url = href if href.startswith("http") else f"https://devpost.com{href}"
                items.append({
                    "source": "Devpost Hyderabad",
                    "category": "hackathon",
                    "title": clean_title,
                    "url": full_url,
                    "summary": f"In-person & hybrid hackathon in the Hyderabad ecosystem: {clean_title}.",
                    "date_str": "",
                    "deadline": "Register on Devpost",
                    "location": "Hyderabad",
                    "eligibility": "Open to developers and students",
                })
    except Exception as e:
        logger.warning(f"Error parsing Devpost Hyderabad hackathons: {e}")

    logger.info(f"Collected {len(items)} hackathon items from Devpost Hyderabad")
    return items

def fetch_hyderabad_venue_events(limit: int = config.MAX_ITEMS_PER_FEED) -> List[Dict[str, Any]]:
    """
    Monitors key Hyderabad offline hubs (T-Hub Phase 2, IIIT Hyderabad CIE, DevX, WeWork Kondapur/Nanakramguda, Hitec City, Gachibowli).
    """
    venue_targets = [
        ("T-Hub", "https://t-hub.co/events/", "T-Hub Phase 2, Gachibowli"),
        ("IIIT Hyderabad", "https://cie.iiit.ac.in/events/", "IIIT Hyderabad, Gachibowli"),
        ("DevX", "https://www.devx.work/events/", "DevX, Hitec City"),
        ("WeWork Kondapur", "https://wework.co.in/events/", "WeWork Kondapur, Hyderabad"),
        ("WeWork Nanakramguda", "https://wework.co.in/locations/hyderabad/nanakramguda/", "WeWork Nanakramguda, Hyderabad"),
    ]

    items: List[Dict[str, Any]] = []
    for hub_name, hub_url, venue_label in venue_targets:
        if len(items) >= limit:
            break
        data = fetch_data(hub_url, timeout=min(config.HTTP_TIMEOUT, 5))
        if not data:
            continue
        try:
            html_text = data.decode("utf-8", errors="ignore")
            # Parse headings or event links
            matches = re.findall(r'<h[2-5][^>]*>([^<]{8,100})</h[2-5]>', html_text)
            if not matches:
                matches = re.findall(r'<a[^>]+href="[^"]*events?[^"]*"[^>]*>([^<]{8,100})</a>', html_text)

            for h in matches:
                if len(items) >= limit:
                    break
                clean_h = strip_html_tags(h).strip()
                if any(skip in clean_h.lower() for skip in ("subscribe", "newsletter", "contact", "about", "footer", "menu", "privacy", "cookie", "login")):
                    continue
                items.append({
                    "source": f"{hub_name} Monitor",
                    "category": "event",
                    "title": clean_h,
                    "url": hub_url,
                    "summary": f"Offline tech workshop or meetup hosted at {venue_label}.",
                    "date_str": "",
                    "deadline": f"Check {hub_name} event portal",
                    "location": f"Hyderabad ({venue_label})",
                    "eligibility": "Developers, founders, and students",
                })
        except Exception as e:
            logger.warning(f"Error monitoring venue {hub_name}: {e}")

    logger.info(f"Collected {len(items)} items from Hyderabad Venue Monitor")
    return items

def collect_hyderabad_items(limit_per_feed: int = config.MAX_ITEMS_PER_FEED) -> List[Dict[str, Any]]:
    """
    Executes all Hyderabad-specific collectors resiliently and returns combined items.
    """
    hyd_collectors = [
        ("Commudle Hyderabad", fetch_commudle_events),
        ("Luma Hyderabad", fetch_luma_hyderabad_events),
        ("Meetup Hyderabad", fetch_meetup_hyderabad_events),
        ("Unstop Hackathons", fetch_unstop_hackathons),
        ("Devpost Hyderabad", fetch_devpost_hyderabad_hackathons),
        ("Hyderabad Venue Monitor", fetch_hyderabad_venue_events),
    ]
    items: List[Dict[str, Any]] = []
    for name, fn in hyd_collectors:
        try:
            results = fn(limit=limit_per_feed)
            items.extend(results)
        except Exception as e:
            logger.error(f"Hyderabad collector '{name}' failed with error: {e}", exc_info=True)
    return items

def collect_all_items(
    limit_per_feed: int = config.MAX_ITEMS_PER_FEED,
    include_legion_feed: bool = False,
    include_hyderabad: bool = True
) -> List[Dict[str, Any]]:
    """
    Executes all collector functions resiliently and returns a single combined list of items.
    A failure in any single collector does not halt the overall collection.
    """
    all_items = []
    collectors = [
        ("Hackathons", fetch_devpost_hackathons),
        ("Tech News", fetch_hacker_news),
        ("Courses & Learning", fetch_reddit_courses),
        ("WeWorkRemotely Jobs", fetch_weworkremotely_jobs),
        ("RemoteOK Jobs", fetch_remoteok_jobs),
        ("Tech Events", fetch_tech_events),
    ]
    if include_hyderabad:
        collectors.extend([
            ("Commudle Hyderabad", fetch_commudle_events),
            ("Luma Hyderabad", fetch_luma_hyderabad_events),
            ("Meetup Hyderabad", fetch_meetup_hyderabad_events),
            ("Unstop Hackathons", fetch_unstop_hackathons),
            ("Devpost Hyderabad", fetch_devpost_hyderabad_hackathons),
            ("Hyderabad Venue Monitor", fetch_hyderabad_venue_events),
        ])
    if include_legion_feed:
        collectors.append(("Legion Feed", fetch_legion_feed_items))
    
    for name, collector_fn in collectors:
        try:
            results = collector_fn(limit=limit_per_feed)
            all_items.extend(results)
        except Exception as e:
            logger.error(f"Collector '{name}' failed with error: {e}", exc_info=True)
            
    logger.info(f"Total collected items across all feeds: {len(all_items)}")
    return all_items


if __name__ == "__main__":
    print("Testing collectors...")
    items = collect_all_items(limit_per_feed=2)
    print(f"\nCollected {len(items)} test items:")
    for i, it in enumerate(items, 1):
        clean_title = it['title'].encode('ascii', errors='replace').decode('ascii')
        print(f"{i}. [{it['category'].upper()}] {clean_title} ({it['source']}) -> {it['url']}")
