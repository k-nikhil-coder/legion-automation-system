"""
Legion Automation System - Detail Page Enricher Module
Extracts rich structured metadata from detail pages using:
1) schema.org JSON-LD
2) OpenGraph & meta tags
3) Regex over visible text
"""

import html
import json
import logging
import re
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

import config
import db

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

logger = logging.getLogger("legion.enricher")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s] %(message)s")

# SSL Context
try:
    import certifi
    SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:
    SSL_CTX = ssl.create_default_context()

# Rate limiting per host: max 1 request per second
_host_last_req: Dict[str, float] = {}
_rate_lock = threading.Lock()

# 24-hour URL cache: url -> (timestamp, html_str)
_url_cache: Dict[str, Tuple[float, str]] = {}
_cache_lock = threading.Lock()

CACHE_TTL_SECONDS = 86400  # 24 hours

TAG_RE = re.compile(r"<[^>]+>")
MARKETING_FLUFF_RE = re.compile(
    r"\b(don\'?t miss out|register now!?|hurry up!?|limited seats!?|exciting opportunity!?|"
    r"world[- ]class|leading[- ]edge|best in class|sign up today!?|join us now!?|act fast!?|"
    r"what are you waiting for|exclusive access)\b",
    re.IGNORECASE
)
EMOJI_RE = re.compile(
    r"[\U00010000-\U0010ffff\u2600-\u27bf\u2300-\u23ff\u2b50-\u2b55\u200d\ufe0f]"
)


def strip_html_tags(text: str) -> str:
    """Strips HTML tags, script/style blocks, comments, and unescapes entities."""
    if not text:
        return ""
    clean = re.sub(r"<(script|style)[^>]*>[\s\S]*?</\1>", " ", text, flags=re.IGNORECASE)
    clean = re.sub(r"<!--[\s\S]*?-->", " ", clean)
    clean = re.sub(r"<(?:p|div|br|li|h[1-6]|tr|td)(?:\s+[^>]*>|>)", " ", clean, flags=re.IGNORECASE)
    clean = TAG_RE.sub(" ", clean)
    clean = html.unescape(clean)
    return " ".join(clean.split())


def summarize(text: str, max_len: int = config.SUMMARY_MAX) -> str:
    """
    Produces 1-2 sentence concise summary:
    - <= max_len chars (default 220)
    - No marketing fluff
    - No emojis
    - No ALL CAPS words
    """
    if not text:
        return ""
    s = str(text).strip()
    s = strip_html_tags(s)
    # Remove emojis
    s = EMOJI_RE.sub("", s)
    # Remove marketing fluff
    s = MARKETING_FLUFF_RE.sub("", s)
    # Normalize whitespace
    s = " ".join(s.split())
    # Normalize ALL CAPS words (len >= 3)
    words = []
    for w in s.split():
        if len(w) >= 3 and w.isupper() and w.isalpha():
            words.append(w.capitalize())
        else:
            words.append(w)
    s = " ".join(words)

    # Extract 1-2 sentences
    sentences = re.split(r'(?<=[.!?])\s+', s)
    candidate = ""
    for sent in sentences[:2]:
        if not candidate:
            candidate = sent
        elif len(candidate) + 1 + len(sent) <= max_len:
            candidate = f"{candidate} {sent}"
        else:
            break

    if not candidate:
        candidate = s

    if len(candidate) > max_len:
        if max_len <= 3:
            return candidate[:max_len]
        # Cut at sentence or word boundary
        cut = candidate[:max_len - 3]
        last_space = cut.rfind(" ")
        if last_space > 40:
            candidate = cut[:last_space] + "..."
        else:
            candidate = cut + "..."

    return candidate.strip()


COMPENSATION_RE = re.compile(
    r"(?:₹\s?\d+[\d,\.]*\s?(?:LPA|Lakhs?|Cr|Crores?|K|k|/month|/mo|per month)?|\$\d+[\d,\.]*\s?(?:k|K|/yr|/mo)?)",
    re.IGNORECASE
)

CORE_SKILLS = [
    "Python", "C++", "SQL", "Docker", "FastAPI", "React", "AWS", "GCP",
    "PyTorch", "Scikit-Learn", "XGBoost", "LangChain", "LLM", "RAG", "Rust",
    "Go", "Kubernetes", "Linux", "Git", "TypeScript", "Java", "JavaScript",
    "Node.js", "Next.js", "Django", "Flask", "Pandas", "NumPy", "TensorFlow",
    "Keras", "HuggingFace", "Terraform", "Azure", "MongoDB", "PostgreSQL",
    "Redis", "GraphQL", "CI/CD", "HTML", "CSS"
]

PERK_KEYWORDS = [
    "Free Certificate", "Swag", "Cloud Credits", "PPI", "PPO",
    "Mentorship", "Lunch Provided", "Networking", "Remote Work"
]


def extract_compensation(text: str) -> str:
    """Finds pay, stipends, and prize amounts from text."""
    if not text:
        return ""
    m = COMPENSATION_RE.search(str(text))
    return m.group(0).strip() if m else ""


def extract_skill_tags(text: str) -> str:
    """Identifies technical skills matching 40 core skills dictionary (up to 5 comma-separated)."""
    if not text:
        return ""
    s = str(text)
    matched = []
    for skill in CORE_SKILLS:
        pattern = r'\b' + re.escape(skill) + r'\b'
        if re.search(pattern, s, re.IGNORECASE):
            matched.append(skill)
            if len(matched) >= 5:
                break
    return ", ".join(matched)


def extract_eligibility(text: str, category: str = "events") -> str:
    """Identifies target participants: batch years, degree types, or experience levels."""
    if not text:
        return "Open to Students & Developers"
    s = str(text)
    batches = re.findall(r'\b(202[5-8])\b', s)
    degrees = re.findall(r'\b(B\.?Tech|M\.?Tech|B\.?E\.?|MCA|All Students)\b', s, re.IGNORECASE)
    exp = re.findall(r'\b(0-2\s*years|0-1\s*years|freshers?|open to all)\b', s, re.IGNORECASE)

    parts = []
    if degrees:
        deg_str = " / ".join(sorted(set(degrees)))
        if batches:
            deg_str += f" ({', '.join(sorted(set(batches)))} Batch)"
        parts.append(deg_str)
    elif batches:
        parts.append(f"{', '.join(sorted(set(batches)))} Batch")

    if exp:
        parts.append(exp[0].title())

    if parts:
        return " | ".join(parts)
    return "Open to Students & Developers"


def extract_perks(text: str, category: str = "events") -> str:
    """Identifies key benefits (up to 3 comma-separated perks)."""
    if not text:
        return ""
    s = str(text)
    matched = []
    for perk in PERK_KEYWORDS:
        pattern = r'\b' + re.escape(perk) + r'\b'
        if re.search(pattern, s, re.IGNORECASE):
            matched.append(perk)
            if len(matched) >= 3:
                break
    return ", ".join(matched)


def detect_city(text: str) -> str:
    """
    Normalizes city to hyderabad | bengaluru | online | other.
    'Bangalore' and 'Bengaluru' both map to bengaluru.
    """
    return config.normalize_city(text)


def fetch_page(url: str, timeout: int = config.HTTP_TIMEOUT) -> Optional[str]:
    """
    Fetches HTML content with per-host rate limit (max 1 req/sec) and 24h cache.
    Resilient: survives timeouts, 404s, connection errors without raising.
    """
    if not url or not isinstance(url, str):
        return None
    url = url.strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        return None

    now = time.time()
    # Check cache
    with _cache_lock:
        if url in _url_cache:
            ts, content = _url_cache[url]
            if now - ts < CACHE_TTL_SECONDS:
                return content

    # Rate limit by host
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower()
    if host:
        with _rate_lock:
            last = _host_last_req.get(host, 0.0)
            elapsed = now - last
            if elapsed < 1.0:
                time.sleep(1.0 - elapsed)
            _host_last_req[host] = time.time()

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": config.USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        }
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as resp:
            raw = resp.read()
            html_text = raw.decode("utf-8", errors="replace")
            with _cache_lock:
                _url_cache[url] = (time.time(), html_text)
            return html_text
    except Exception as e:
        logger.debug(f"Fetch detail page failed for {url}: {e}")
        return None


def _extract_json_ld(html_text: str) -> List[Dict[str, Any]]:
    """Extracts all schema.org JSON-LD objects."""
    results: List[Dict[str, Any]] = []
    if not html_text:
        return results

    matches = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>([\s\S]*?)</script>', html_text, re.IGNORECASE)
    for m in matches:
        raw_json = m.strip()
        if not raw_json:
            continue
        try:
            data = json.loads(raw_json)
            if isinstance(data, list):
                results.extend(d for d in data if isinstance(d, dict))
            elif isinstance(data, dict):
                if "@graph" in data and isinstance(data["@graph"], list):
                    results.extend(d for d in data["@graph"] if isinstance(d, dict))
                else:
                    results.append(data)
        except Exception:
            pass
    return results


def _extract_meta_tags(html_text: str) -> Dict[str, str]:
    """Extracts OpenGraph and standard meta tags."""
    meta: Dict[str, str] = {}
    if not html_text:
        return meta

    pattern = re.compile(r'<meta\s+([^>]+)>', re.IGNORECASE)
    for tag in pattern.findall(html_text):
        name_match = re.search(r'(?:name|property)=["\']([^"\']+)["\']', tag, re.IGNORECASE)
        content_match = re.search(r'content=["\']([^"\']*)["\']', tag, re.IGNORECASE)
        if name_match and content_match:
            k = name_match.group(1).lower().strip()
            v = html.unescape(content_match.group(1).strip())
            if k and v:
                meta[k] = v
    return meta


def extract_metadata(html_text: str, base_url: str = "", category: str = "news") -> Dict[str, Any]:
    """
    Extracts structured fields in priority order:
    1) schema.org JSON-LD (Event, JobPosting, Course)
    2) OpenGraph / meta tags
    3) Regex over visible text
    Only fields actually found on page are included (never invented).
    """
    fields: Dict[str, Any] = {}
    if not html_text:
        return fields

    # 1. Schema.org JSON-LD
    json_ld_list = _extract_json_ld(html_text)
    for obj in json_ld_list:
        otype = str(obj.get("@type") or obj.get("type") or "").lower()
        
        # Events & Hackathons
        if any(t in otype for t in ("event", "hackathon")):
            if "startDate" in obj and not fields.get("event_start"):
                fields["event_start"] = str(obj["startDate"])
            if "endDate" in obj and not fields.get("event_end"):
                fields["event_end"] = str(obj["endDate"])
            if "organizer" in obj and not fields.get("organizer"):
                org = obj["organizer"]
                fields["organizer"] = org.get("name") if isinstance(org, dict) else str(org)
            if "eventAttendanceMode" in obj and not fields.get("mode"):
                mode_str = str(obj["eventAttendanceMode"]).lower()
                if "offline" in mode_str:
                    fields["mode"] = "OFFLINE"
                elif "online" in mode_str:
                    fields["mode"] = "ONLINE"
                elif "mixed" in mode_str:
                    fields["mode"] = "HYBRID"
            if "location" in obj:
                loc = obj["location"]
                if isinstance(loc, dict):
                    loc_name = loc.get("name", "")
                    addr = loc.get("address", {})
                    addr_str = addr.get("addressLocality") if isinstance(addr, dict) else str(addr)
                    if loc_name and not fields.get("venue"):
                        fields["venue"] = loc_name
                    if addr_str and not fields.get("city"):
                        fields["city"] = detect_city(addr_str)
            if "offers" in obj and not fields.get("fee"):
                offers = obj["offers"]
                if isinstance(offers, dict):
                    price = offers.get("price")
                    if str(price) == "0" or offers.get("isAccessibleForFree") is True:
                        fields["fee"] = "Free"
                    elif price:
                        curr = offers.get("priceCurrency", "₹")
                        sym = "₹" if curr in ("INR", "₹") else curr
                        fields["fee"] = f"{sym}{price}"

            if "prize" in obj and not fields.get("prize"):
                fields["prize"] = str(obj["prize"])
            elif "award" in obj and not fields.get("prize"):
                fields["prize"] = str(obj["award"])

        # JobPosting
        if "jobposting" in otype:
            if "title" in obj and not fields.get("role"):
                fields["role"] = str(obj["title"])
            if "hiringOrganization" in obj and not fields.get("company"):
                h = obj["hiringOrganization"]
                fields["company"] = h.get("name") if isinstance(h, dict) else str(h)
            if "validThrough" in obj and not fields.get("deadline"):
                fields["deadline"] = str(obj["validThrough"])
            if "baseSalary" in obj and not fields.get("stipend"):
                bs = obj["baseSalary"]
                if isinstance(bs, dict):
                    val = bs.get("value", {})
                    fields["stipend"] = str(val.get("value") or bs.get("value") or "")
                else:
                    fields["stipend"] = str(bs)
            if "experienceRequirements" in obj and not fields.get("experience"):
                fields["experience"] = str(obj["experienceRequirements"])
            if "jobLocation" in obj and not fields.get("location"):
                jl = obj["jobLocation"]
                if isinstance(jl, dict):
                    addr = jl.get("address", {})
                    fields["location"] = addr.get("addressLocality") if isinstance(addr, dict) else str(addr)

        # Course
        if "course" in otype:
            if "provider" in obj and not fields.get("organizer"):
                pr = obj["provider"]
                fields["organizer"] = pr.get("name") if isinstance(pr, dict) else str(pr)
            if "offers" in obj and not fields.get("fee"):
                offers = obj["offers"]
                if isinstance(offers, dict):
                    price = offers.get("price")
                    if str(price) == "0" or offers.get("isAccessibleForFree") is True:
                        fields["fee"] = "Free"
                    elif price:
                        curr = offers.get("priceCurrency", "₹")
                        sym = "₹" if curr in ("INR", "₹") else curr
                        fields["fee"] = f"{sym}{price}"

        # Common schema.org fields: description and url (Priority 1)
        if "description" in obj and not fields.get("summary"):
            desc_val = obj["description"]
            if isinstance(desc_val, str) and desc_val.strip():
                fields["summary"] = strip_html_tags(desc_val)
        if "url" in obj and not fields.get("apply_url"):
            u_val = obj["url"]
            if isinstance(u_val, str) and u_val.startswith("http"):
                fields["apply_url"] = u_val
        elif "applyUrl" in obj and not fields.get("apply_url"):
            u_val = obj["applyUrl"]
            if isinstance(u_val, str) and u_val.startswith("http"):
                fields["apply_url"] = u_val

    # 2. OpenGraph / Meta Tags
    meta = _extract_meta_tags(html_text)
    if "og:description" in meta and not fields.get("summary"):
        fields["summary"] = meta["og:description"]
    elif "description" in meta and not fields.get("summary"):
        fields["summary"] = meta["description"]

    if "og:url" in meta and not fields.get("apply_url"):
        fields["apply_url"] = meta["og:url"]

    # 3. Regex over visible text
    plain_text = strip_html_tags(html_text)

    # Event Start / End dates
    if not fields.get("event_start"):
        m_start = re.search(r'\b(?:starts?|date|when):\s*([A-Za-z0-9, ]{6,30})\b', plain_text, re.IGNORECASE)
        if m_start:
            dt_p = db.parse_date_ist(m_start.group(1))
            if dt_p:
                fields["event_start"] = dt_p.strftime("%Y-%m-%d")

    # Deadline
    if not fields.get("deadline"):
        m_dl = re.search(r'\b(?:deadline|apply by|last date):\s*([A-Za-z0-9, ]{6,30})\b', plain_text, re.IGNORECASE)
        if m_dl:
            dt_p = db.parse_date_ist(m_dl.group(1))
            if dt_p:
                fields["deadline"] = dt_p.strftime("%Y-%m-%d")

    # Mode
    if not fields.get("mode"):
        if re.search(r'\b(online|virtual|webinar|remote|zoom|google meet)\b', plain_text, re.IGNORECASE):
            fields["mode"] = "ONLINE"
        elif re.search(r'\b(in-person|offline|in person|walk-in)\b', plain_text, re.IGNORECASE):
            fields["mode"] = "OFFLINE"
        elif re.search(r'\bhybrid\b', plain_text, re.IGNORECASE):
            fields["mode"] = "HYBRID"

    # City & Venue
    if not fields.get("city"):
        fields["city"] = detect_city(plain_text)

    if not fields.get("venue"):
        # Check Hyderabad venues
        import formatter
        v_hyd = formatter.detect_hyderabad_venue(plain_text)
        if v_hyd:
            fields["venue"] = v_hyd
            if not fields.get("city") or fields.get("city") == "other":
                fields["city"] = "hyderabad"

    # Fee
    if not fields.get("fee"):
        if re.search(r'\b(?:free\s+registration|free\s+entry|no\s+entry\s+fee|entry:\s*free)\b', plain_text, re.IGNORECASE):
            fields["fee"] = "Free"
        else:
            m_fee = re.search(r'\b(?:fee|ticket|entry|price):\s*(?:₹|inr|rs\.?)?\s*(\d+)\b', plain_text, re.IGNORECASE)
            if m_fee:
                fields["fee"] = f"₹{m_fee.group(1)}"

    # Prize
    if not fields.get("prize"):
        m_prize = re.search(r'\b(?:prize\s*pool|total\s*prizes?|cash\s*prize):\s*([₹$\w, ]{3,20})\b', plain_text, re.IGNORECASE)
        if m_prize:
            fields["prize"] = m_prize.group(1).strip()

    # Eligibility
    if not fields.get("eligibility"):
        m_elig = re.search(r'\b(?:eligibility|who\s*can\s*apply):\s*([^\n\r.]+)\b', plain_text, re.IGNORECASE)
        if m_elig:
            fields["eligibility"] = m_elig.group(1).strip()[:50]
        elif re.search(r'\b(?:open to all|students|working professionals|freshers)\b', plain_text, re.IGNORECASE):
            m_kw = re.search(r'\b(open to all|students|working professionals|freshers)\b', plain_text, re.IGNORECASE)
            if m_kw:
                fields["eligibility"] = m_kw.group(1).capitalize()
        else:
            fields["eligibility"] = extract_eligibility(plain_text, category)

    # Compensation & Perks & Skills
    if not fields.get("compensation"):
        fields["compensation"] = extract_compensation(plain_text)
    if not fields.get("skill_tags"):
        fields["skill_tags"] = extract_skill_tags(plain_text)
    if not fields.get("perks"):
        fields["perks"] = extract_perks(plain_text, category)

    # Job-specific fields
    if category == "job":
        if not fields.get("company"):
            m_comp = re.search(r'\b(?:company|employer|hiring organization):\s*([A-Za-z0-9&., ]{2,40})\b', plain_text, re.IGNORECASE)
            if m_comp:
                fields["company"] = m_comp.group(1).strip()
        if not fields.get("role"):
            m_role = re.search(r'\b(?:role|position|job title):\s*([A-Za-z0-9&., ]{2,50})\b', plain_text, re.IGNORECASE)
            if m_role:
                fields["role"] = m_role.group(1).strip()
        if not fields.get("stipend"):
            m_stip = re.search(r'\b(?:stipend|salary|ctc):\s*([₹$\w, /-]+)\b', plain_text, re.IGNORECASE)
            if m_stip:
                fields["stipend"] = m_stip.group(1).strip()[:30]
        if not fields.get("experience"):
            m_exp = re.search(r'\b(\d+\+?\s*years?(?:\s+of)?\s+experience|freshers?|entry[- ]level|senior)\b', plain_text, re.IGNORECASE)
            if m_exp:
                fields["experience"] = m_exp.group(1).strip()

    return fields


def enrich_item(item: Dict[str, Any], timeout: int = config.HTTP_TIMEOUT) -> Dict[str, Any]:
    """
    Enriches item by fetching detail page.
    Guaranteed never to crash (per-item try/except).
    Stores extracted fields as columns & JSON in enriched_data.
    """
    if not isinstance(item, dict):
        return {}

    enriched = dict(item)
    try:
        url = enriched.get("url") or enriched.get("link") or ""
        cat = enriched.get("category", "news").lower()

        # Fetch detail page
        html_page = fetch_page(url, timeout=timeout) if url else None
        extracted = extract_metadata(html_page, base_url=url, category=cat) if html_page else {}

        # Merge extracted fields only if present and not already non-empty
        for k, v in extracted.items():
            if v and (k not in enriched or not enriched[k]):
                enriched[k] = v

        # If summary on item is empty or long, summarize
        raw_summary = enriched.get("summary") or extracted.get("summary") or enriched.get("title", "")
        enriched["summary"] = summarize(raw_summary, max_len=config.SUMMARY_MAX)

        # Normalize city
        city_raw = enriched.get("city") or enriched.get("location") or ""
        enriched["city"] = detect_city(city_raw)

        # Mode
        if not enriched.get("mode"):
            if enriched.get("city") == "online" or "online" in str(enriched.get("location", "")).lower():
                enriched["mode"] = "ONLINE"
            elif enriched.get("city") in ("hyderabad", "bengaluru"):
                enriched["mode"] = "OFFLINE"

        # Badge
        if enriched.get("city") == "hyderabad":
            enriched["badge"] = "HYDERABAD - OFFLINE"
        elif enriched.get("city") == "bengaluru":
            enriched["badge"] = "BENGALURU - OFFLINE"
        elif enriched.get("city") == "online" or enriched.get("mode") == "ONLINE":
            enriched["badge"] = "ONLINE"

        # Rule: If no parseable event_start/deadline for event or hackathon -> status='review', reason='no date'
        if cat in ("event", "hackathon"):
            has_date = bool(
                enriched.get("event_start") or
                enriched.get("deadline") or
                enriched.get("date_str")
            )
            # Check if any parseable date can be extracted
            if not has_date or not db.parse_date_ist(enriched.get("deadline") or enriched.get("event_start") or enriched.get("date_str")):
                enriched["status"] = "review"
                enriched["reason"] = "no date"
                enriched["eligibility"] = "Review needed: no date"

        # Compute fingerprint and expires_at
        fp = db.compute_fingerprint(
            title=enriched.get("title", ""),
            start_date=enriched.get("event_start") or enriched.get("date_str") or enriched.get("deadline") or "",
            city=enriched.get("city") or ""
        )
        enriched["fingerprint"] = fp

        exp_str, computed_status = db.compute_expires_at(enriched)
        enriched["expires_at"] = exp_str
        if enriched.get("status") not in ("review", "rejected"):
            enriched["status"] = computed_status

        # Extract v3 fields if missing
        context_text = f"{enriched.get('title', '')} {enriched.get('summary', '')}"
        if not enriched.get("compensation"):
            comp_found = extract_compensation(context_text)
            if comp_found:
                enriched["compensation"] = comp_found
        if not enriched.get("skill_tags"):
            skills_found = extract_skill_tags(context_text)
            if skills_found:
                enriched["skill_tags"] = skills_found
        if not enriched.get("perks"):
            perks_found = extract_perks(context_text, cat)
            if perks_found:
                enriched["perks"] = perks_found
        if not enriched.get("eligibility") or enriched.get("eligibility") == "Open to all":
            elig_found = extract_eligibility(context_text, cat)
            if elig_found and elig_found != "Open to Students & Developers":
                enriched["eligibility"] = elig_found

        # Pack enriched structured fields into JSON
        clean_extracted = {
            k: enriched.get(k)
            for k in (
                "organizer", "event_start", "event_end", "deadline", "mode",
                "city", "venue", "fee", "prize", "eligibility", "apply_url",
                "company", "role", "stipend", "experience", "expires_at", "fingerprint",
                "sub_category", "experience_level", "compensation", "skill_tags", "perks", "relevance_score"
            )
            if enriched.get(k) is not None
        }
        enriched["enriched_data"] = json.dumps(clean_extracted, ensure_ascii=False)

    except Exception as e:
        logger.warning(f"Error enriching item '{item.get('title')}': {e}")
        # Always return item safely without raising
        return item

    return enriched
