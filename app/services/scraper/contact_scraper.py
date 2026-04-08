# app/services/scraper/contact_scraper.py
import logging
import asyncio
import re
import random
from typing import List, Dict, Any, Optional
import httpx
from ddgs import DDGS

from apify_client import ApifyClient
from app.models.lead import LeadProfile
from app.config.settings import settings

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# STEALTH DDG IDENTITY POOL (mirrors social_resolver)
# ─────────────────────────────────────────────
_DDG_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_3) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
]
_DDG_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
    "Connection": "keep-alive",
}

# ─────────────────────────────────────────────
# PRODUCTION URL INTELLIGENCE
# ─────────────────────────────────────────────

# Tier-1: Hard blocklist — domains that NEVER contain real contact data.
# These sites are either gated (login required), aggregators, or traps.
_HARD_BLOCKED_DOMAINS = [
    # Social / messaging
    'linkedin.com', 'instagram.com', 'twitter.com', 'youtube.com',
    'facebook.com', 'whatsapp.com', 'telegram.org',
    # B2B intelligence databases
    'dnb.com', 'zoominfo.com', 'crunchbase.com', 'apollo.io',
    'contactout.com', 'lusha.com', 'hunter.io', 'clearbit.com',
    # Indian business directories (for deep crawl — see directory fallback for targeted use)
    'indiamart.com', 'dir.indiamart.com', 'justdial.com', 'sulekha.com',
    'tradeindia.com', 'exportersindia.com', 'indialog.com',
    'hotfrog.in', 'hotfrog.com',
    'ebatterydirectory.com', 'solaralmanac.info',
    'enfsolar.com', 'esunsolar.in', 'renewableaffairs.com',
    # Solar-specific directories / marketplaces (contain contact forms, not raw phone numbers)
    'solarmango.com', 'solarquarter.com', 'mercomin.com', 'mercomcapital.com',
    'solarwala.com', 'kompass.com', 'unergia.in', 'solartrade.in',
    'indiagosolarmmaket.com', 'bridge2india.com',
    # Government portals (PDFs only, not structured data)
    'pmsuryaghar.gov.in', 'mnre.gov.in', 'rrecl.rajasthan.gov.in',
    'nredcap.in', 'upneda.org.in',
    # Company registries (no phone numbers, just filings)
    'zaubacorp.com', 'tracxn.com', 'tofler.in', 'mycorporateinfo.com',
    'indiafilings.com', 'mca.gov.in', 'roc.gov.in',
    # Yellow pages / directories
    'yellowpages.in', 'yellowpagesindia.com', 'yelp.com',
    'omanyellowpagesonline.com', 'yellowpages.com',
    # News / media
    'economictimes.indiatimes.com', 'livemint.com', 'moneycontrol.com',
    'business-standard.com', 'thehindu.com', 'solarquarter.com',
    # Large corporate parent domains (not local offices)
    'tatamotors.com', 'mahindra.com', 'ashokleyland.com',
]

# Tier-2: Directory-like URL path patterns — even if domain passes, skip these paths.
_DIRECTORY_PATH_PATTERNS = [
    r'/search/', r'/listing/', r'/company/', r'/business/',
    r'/profile/', r'/find/', r'/directory/',
]


def _classify_url(url: str, lead: LeadProfile) -> int:
    """
    Score a candidate URL for quality as a deep-crawl start point.
    Returns: 100 = official site, 50 = name-match, 10 = unknown clean, -1 = skip.

    This replaces the brittle static blocklist approach with intelligent scoring
    based on the lead's own data, making it adaptive without code changes.
    """
    url_lower = url.lower()

    # Extract domain from URL
    domain_match = re.search(r'https?://(?:www\.)?([^/]+)', url_lower)
    domain = domain_match.group(1) if domain_match else ""

    # Hard block — never crawl these
    if any(blocked in domain for blocked in _HARD_BLOCKED_DOMAINS):
        return -1

    # Directory path patterns — skip even on clean domains
    if any(re.search(p, url_lower) for p in _DIRECTORY_PATH_PATTERNS):
        return -1

    # Priority 1: Lead's own official website (highest signal)
    if lead.website_url:
        official_domain = re.sub(r'https?://(?:www\.)?', '', lead.website_url.lower()).split('/')[0]
        if official_domain and official_domain in domain:
            return 100

    # Priority 2: Lead name words appear in domain (likely official site)
    name_words = [w for w in re.findall(r'[a-z]+', lead.name.lower()) if len(w) > 2]
    name_words = [w for w in name_words if w not in {'the', 'and', 'india', 'pvt', 'ltd', 'llp'}]
    if any(w in domain for w in name_words):
        return 50

    # Priority 3: Unknown but not blocked — allow with low score
    return 10


def _rank_start_urls(candidates: List[Dict[str, str]], lead: LeadProfile) -> List[Dict[str, str]]:
    """
    Score, filter, and sort candidate URLs before passing to Apify.
    Returns at most 2 URLs, highest-scored first.
    """
    scored = []
    for item in candidates:
        url = item.get('url', '')
        score = _classify_url(url, lead)
        if score > 0:
            scored.append((score, item))

    # Sort by score descending, take top 2
    scored.sort(key=lambda x: x[0], reverse=True)
    ranked = [item for _, item in scored[:2]]

    if ranked:
        logger.info(f"[{lead.name}] URL Scorer: selected {len(ranked)} start URLs | "
                    f"Top: {ranked[0]['url']}")
    return ranked


async def _get_directory_phone_fallback(lead: LeadProfile) -> List[Dict[str, str]]:
    """
    LAST RESORT: Targeted DDG dork against JustDial, IndiaMart, and Sulekha.

    Fires ONLY when:
      - The main pipeline (Tavily/DDG + Apify) found zero phone numbers.
      - The lead has no official website URL.

    These directories are intentionally blocked from the deep Apify crawl (too noisy),
    but their search snippet text is rich enough to extract a phone number directly
    using PHONE_REGEX — no browser/crawl needed.

    Returns a list of snippet-sourced phone dicts: [{"phone": raw, "url": source}]
    """
    logger.warning(
        f"[{lead.name}] ⚠️ No phone found yet & no official site — "
        f"activating JustDial/IndiaMart Last-Resort Fallback..."
    )

    found_phones: List[Dict[str, str]] = []
    name_words = lead.name.lower().split()[:2]

    # These three directories reliably show phone numbers in DDG search snippets
    # for Indian businesses — even without crawling the page itself.
    DIRECTORY_DORK_TEMPLATES = [
        f'"{lead.name}" "{lead.state}" site:justdial.com',
        f'"{lead.name}" "{lead.state}" site:indiamart.com',
        f'"{lead.name}" "{lead.state}" site:sulekha.com',
    ]

    headers = _DDG_HEADERS.copy()
    headers["User-Agent"] = random.choice(_DDG_USER_AGENTS)

    for query in DIRECTORY_DORK_TEMPLATES:
        if len(found_phones) >= 2:  # Stop early if we already have 2 numbers
            break

        def _search(q=query):
            try:
                with DDGS(headers=headers, timeout=20) as ddgs:
                    return list(ddgs.text(q, max_results=5))
            except Exception as e:
                logger.debug(f"Directory fallback DDG error: {e}")
                return []

        await asyncio.sleep(random.uniform(1.0, 2.5))  # Stealth delay between queries
        results = await asyncio.to_thread(_search)

        for result in (results or []):
            snippet = result.get("body", "")
            source_url = result.get("href", "")
            snippet_lower = snippet.lower()

            for match in PHONE_REGEX.finditer(snippet):
                phone_val = match.group().strip()
                pos = match.start()
                # 80-char proximity window — same as main pipeline
                context = snippet_lower[max(0, pos - 80): min(len(snippet_lower), pos + 80)]

                # Accept only if the business name or state appears near the phone
                if any(word in context for word in name_words) or (
                    lead.state and lead.state.lower() in context
                ):
                    found_phones.append({"phone": phone_val, "url": source_url})
                    logger.info(
                        f"[{lead.name}] ✅ Directory Fallback phone found via "
                        f"{source_url.split('/')[2]}: {phone_val}"
                    )

    return found_phones


def _normalize_phone(raw_phone: str) -> str:
    """Standardizes Indian phone numbers to base 10-digits (or 11 digit landlines)."""
    digits = re.sub(r'\D', '', raw_phone)
    if not digits: return raw_phone
    if digits.startswith('91') and len(digits) == 12:
        return digits[2:] # Keep only the 10 digits
    if digits.startswith('0') and len(digits) == 11:
        return digits # Keep landlines with STD code
    return digits # Return plain digits for others

# Regex for detecting phone numbers
PHONE_REGEX = re.compile(r'(?:\+?91[\-\s]?)?[6789]\d{9}|\+?1?\s*[-.\s]?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}')

async def _get_precision_contacts_via_tavily(lead: LeadProfile) -> tuple[List[Dict[str, str]], List[Dict[str, str]], Optional[str]]:
    """
    Returns:
      1. startUrls for Apify.
      2. List of dicts [{"phone": raw, "url": source_url}] from snippets.
      3. ai_answer (direct AI extraction).
    """
    query = f"What is the contact phone number for {lead.name} in {lead.state} India? Find local branch numbers specifically."
    start_urls = []
    snippet_phones = []
    ai_answer = None
    
    tavily_ok = False
    try:
        async with httpx.AsyncClient() as client:
            res = await client.post(
                "https://api.tavily.com/search",
                headers={"Content-Type": "application/json"},
                json={
                    "api_key": settings.TAVILY_API_KEY,
                    "query": query,
                    "search_depth": "advanced",
                    "max_results": 8,
                    "include_answer": True  # CRITICAL: Get AI-summarized phone
                },
                timeout=15.0
            )

            if res.status_code == 200:
                tavily_ok = True
                data = res.json()
                ai_answer = data.get("answer")

                # EXTRACT FROM SNIPPETS WITH PROXIMITY
                name_words = lead.name.lower().split()[:2]  # Key words to search for

                for result in data.get("results", []):
                    url = result.get("url", "").lower()
                    content = result.get("content", "")
                    content_lower = content.lower()

                    # PROXIMITY CHECK: Only extract phone if name or state is mentioned near it
                    for match in PHONE_REGEX.finditer(content):
                        phone_val = match.group().strip()
                        pos = match.start()

                        # Look at context around the phone number (80-char window)
                        context = content_lower[max(0, pos - 80): min(len(content_lower), pos + 80)]

                        # High-confidence: name or state appears near the phone number
                        if any(word in context for word in name_words) or (lead.state and lead.state.lower() in context):
                            snippet_phones.append({
                                "phone": phone_val,
                                "url": result.get("url")
                            })

                    # FILTER: Use URL scorer instead of static blacklist
                    if _classify_url(result.get("url", ""), lead) > 0:
                        start_urls.append({"url": result["url"]})

                start_urls = start_urls[:2]
            else:
                # Log the actual response body for richer diagnostics (e.g. "Quota exceeded")
                try:
                    body = res.json()
                except Exception:
                    body = res.text
                logger.warning(
                    f"[{lead.name}] Tavily returned HTTP {res.status_code}. "
                    f"Body: {body}. Will trigger DDG fallback."
                )
    except Exception as e:
        logger.error(f"[{lead.name}] Tavily Precision Search exception: {e}")

    return start_urls, snippet_phones, ai_answer, tavily_ok

async def _get_precision_contacts_via_ddg(
    lead: LeadProfile,
) -> tuple[List[Dict[str, str]], List[Dict[str, str]], None]:
    """
    DDG fallback — mirrors Tavily's exact logic:
      - Same precision query targeting local branch numbers.
      - Same 80-char proximity window for phone extraction.
      - Same STARTURL_BLACKLIST filtering for deep-crawl URLs.
      - Scans 15 results (vs Tavily's 8) to compensate for the missing AI answer.
    Returns same 3-tuple signature: (start_urls, snippet_phones, ai_answer=None)
    """
    # Identical query to Tavily — local-branch-specific
    query = (
        f"What is the contact phone number for {lead.name} "
        f"in {lead.state} India? Find local branch numbers specifically."
    )
    start_urls: List[Dict[str, str]] = []
    snippet_phones: List[Dict[str, str]] = []
    name_words = lead.name.lower().split()[:2]

    logger.warning(f"[{lead.name}] ⚡ Tavily unavailable — switching to DDG Precision Fallback...")

    try:
        headers = _DDG_HEADERS.copy()
        headers["User-Agent"] = random.choice(_DDG_USER_AGENTS)

        def _search():
            try:
                with DDGS(headers=headers, timeout=20) as ddgs:
                    # 15 results (vs 8) to compensate for no AI-summarized answer
                    return list(ddgs.text(query, max_results=15))
            except Exception as e:
                logger.debug(f"DDG search error: {e}")
                return []

        # Run blocking DDG call in a thread (keeps async loop free)
        await asyncio.sleep(random.uniform(0.5, 1.5))  # Stealth delay
        results = await asyncio.to_thread(_search)

        for result in (results or []):
            url = result.get("href", "").lower()
            # DDG returns "body" for snippet, Tavily returns "content"
            content = result.get("body", "")
            content_lower = content.lower()

            # ── PROXIMITY CHECK (identical to Tavily's 80-char window) ──
            for match in PHONE_REGEX.finditer(content):
                phone_val = match.group().strip()
                pos = match.start()
                context = content_lower[max(0, pos - 80): min(len(content_lower), pos + 80)]

                # Same acceptance rule: name words OR state must appear in context
                if any(word in context for word in name_words) or (
                    lead.state and lead.state.lower() in context
                ):
                    snippet_phones.append({
                        "phone": phone_val,
                        "url": result.get("href")
                    })

            # ── STARTURL FILTER: use URL scorer instead of static blacklist ──
            if _classify_url(result.get("href", ""), lead) > 0:
                start_urls.append({"url": result["href"]})

        start_urls = start_urls[:2]  # Cap at 2 — same as Tavily
        logger.info(
            f"[{lead.name}] DDG Fallback complete: "
            f"{len(snippet_phones)} phones, {len(start_urls)} start URLs"
        )

    except Exception as e:
        logger.error(f"[{lead.name}] DDG Fallback error: {e}")

    # ai_answer is always None for DDG (no LLM summarisation)
    return start_urls, snippet_phones, None


async def _get_precision_contacts(
    lead: LeadProfile,
) -> tuple[List[Dict[str, str]], List[Dict[str, str]], Optional[str]]:
    """
    Smart dispatcher — single entry point for the orchestrator.
    1. Tries Tavily first (highest quality: ai_answer + snippets + URLs).
    2. Falls back to DDG automatically if:
       - settings.TAVILY_API_KEY is not set.
       - Tavily returns a non-200 status (401, 429, 432, 5xx …).
       - Tavily returns 200 but no results (empty account / filtered query).
    Returns the same 3-tuple regardless of which source was used.
    """
    if not settings.TAVILY_API_KEY:
        logger.warning(f"[{lead.name}] No TAVILY_API_KEY configured — going straight to DDG.")
        return await _get_precision_contacts_via_ddg(lead)

    start_urls, snippet_phones, ai_answer, tavily_ok = await _get_precision_contacts_via_tavily(lead)

    # Trigger DDG if Tavily failed OR returned absolutely nothing useful
    if not tavily_ok or (not start_urls and not snippet_phones and not ai_answer):
        fallback_urls, fallback_phones, _ = await _get_precision_contacts_via_ddg(lead)
        # Merge: DDG supplements Tavily (in case Tavily had partial results)
        start_urls = start_urls or fallback_urls
        snippet_phones = snippet_phones or fallback_phones

    return start_urls, snippet_phones, ai_answer


def _run_apify_contact_actor_sync(
    start_urls: List[Dict[str, str]],
    lead_name: str,
    max_requests: int = 5  # Reduced from 7: URL scorer filters bad pages earlier
) -> Dict[str, Any]:
    """Runs the Apify contact-info scraper with surgical path constraints."""
    if not start_urls:
        return {}

    client = ApifyClient(settings.APIFY_TOKEN)
    run_input = {
        "startUrls": start_urls,
        "maxDepth": 1,           # Was 3. Depth-1 is enough: start page + 1 level of links
        "maxRequests": max_requests,
        "stayWithinDomain": True,
        "extractFromText": True,
        "includeScriptContent": True,
        # Surgical path whitelist: follow ONLY pages likely to have contact info
        "includeGlobs": [
            {"glob": "*contact*"},
            {"glob": "*about*"},
            {"glob": "*reach*"},
            {"glob": "*location*"},
            {"glob": "*address*"},
            {"glob": "*team*"},
            {"glob": "*connect*"},
        ],
        # Path exclusions: skip these even if they appear within an allowed domain
        "excludeGlobs": [
            {"glob": "*product*"},
            {"glob": "*service*"},
            {"glob": "*blog*"},
            {"glob": "*news*"},
            {"glob": "*gallery*"},
            {"glob": "*shop*"},
            {"glob": "*cart*"},
            {"glob": "*price*"},
            {"glob": "*faq*"},
            {"glob": "*career*"},
        ],
    }

    logger.info(
        f"[{lead_name}] Launching Apify contact scraper "
        f"(depth=1, max={max_requests}) on: {start_urls[0]['url']}"
    )
    
    try:
        run = client.actor("vdrmota/contact-info-scraper").call(run_input=run_input)
        if not run or "defaultDatasetId" not in run:
            return {}
            
        dataset_id = run["defaultDatasetId"]
        contacts = {
            "phones": set(),
            "emails": set(),
            "linkedIns": set(),
            "instagrams": set(),
            "twitters": set(),
            "facebooks": set(),
            "phone_list": [] # List of {"phone": raw, "url": source_url}
        }
        
        for item in client.dataset(dataset_id).iterate_items():
            # Individual page info
            page_url = item.get("url")
            agg = item.get("aggregatedResults", {})
            target = agg if agg else item # Handle both summary and individual page formats
            
            for p in target.get("phones", []) + target.get("phonesUncertain", []):
                contacts["phones"].add(p)
                if page_url: contacts["phone_list"].append({"phone": p, "url": page_url})
                
            for e in target.get("emails", []): 
                contacts["emails"].add(e)
            for l in target.get("linkedIns", []): 
                contacts["linkedIns"].add(l)
            for i in target.get("instagrams", []): 
                contacts["instagrams"].add(i)
            for t in target.get("twitters", []): 
                contacts["twitters"].add(t)
            for f in target.get("facebooks", []): 
                contacts["facebooks"].add(f)
                
        return contacts
    except Exception as e:
        logger.error(f"[{lead_name}] Apify Actor execution failed: {e}")
        return {}

async def run_multisite_contact_scraper(lead: LeadProfile) -> LeadProfile:
    """
    Advanced Identity-Aware Precision Scraper:
    1. Uses Tavily AI Answer to pinpoint the specific entity's phone.
    2. Uses Proximity extraction from search snippets and filters out Parent Corporate sites.
    3. Deep-crawls any unique business domain found.
    """
    logger.info(f"[{lead.name}] Starting Precision Persona Scrape...")

    # ── LAYER 0: OFFICIAL SITE INJECTION ──
    # If Google Maps already gave us the official website, use it as the
    # highest-priority start URL. This bypasses search noise entirely.
    official_site_urls: List[Dict[str, str]] = []
    if lead.website_url:
        score = _classify_url(lead.website_url, lead)
        if score > 0:
            official_site_urls = [{"url": lead.website_url}]
            logger.info(f"[{lead.name}] Official site detected: {lead.website_url} (score={score})")

    # STEP 1: AI Discovery + High-Confidence Snippets (Tavily → DDG fallback)
    search_start_urls, tavily_snippet_phones, ai_answer = await _get_precision_contacts(lead)

    # ── MERGE & RANK start URLs ──
    # Official site always goes first (score=100 guaranteed).
    # Search results are scored and filtered through the URL classifier.
    raw_candidates = official_site_urls + search_start_urls
    start_urls = _rank_start_urls(raw_candidates, lead)
    
    # ── CONSENSUS ENGINE: LEDGER INITIALIZATION ──
    # Structure: normalized_phone: { "raw_formats": set(), "score": int, "sources": list({"type", "url"}) }
    phone_ledger: Dict[str, Any] = {}
    
    def _add_to_ledger(raw_phone: str, source_type: str, url: str = None, points: int = 0):
        norm = _normalize_phone(raw_phone)
        if len(norm) < 8: return # Skip invalid short numbers
        
        if norm not in phone_ledger:
            phone_ledger[norm] = {"raw_formats": set(), "score": 0, "sources": []}
            
        phone_ledger[norm]["raw_formats"].add(raw_phone)
        phone_ledger[norm]["score"] += points
        
        # Avoid duplicate source logs
        if not any(s.get("url") == url for s in phone_ledger[norm]["sources"]):
            phone_ledger[norm]["sources"].append({"type": source_type, "url": url})
            
    # Add User Input (Highest Priority)
    if lead.phone:
        for p in lead.phone.split(","):
             _add_to_ledger(p.strip(), "user_input", points=100)

    # Process Tavily Snippets
    for item in tavily_snippet_phones:
        _add_to_ledger(item["phone"], "tavily_snippet", item["url"], points=10)
    
    # If the AI Answer has a phone number, add it
    if ai_answer:
         answers_phones = PHONE_REGEX.findall(ai_answer)
         for ap in answers_phones:
             source_url = start_urls[0]["url"] if start_urls else None
             _add_to_ledger(ap, "tavily_ai_answer", source_url, points=15)
             logger.info(f"[{lead.name}] AI Answer provided phone: {ap}")
    
    # STEP 2: Deep Scrape standalone domain if found
    if start_urls:
         apify_contacts = await asyncio.to_thread(_run_apify_contact_actor_sync, start_urls, lead.name, 7)
         if apify_contacts:
             for item in apify_contacts.get("phone_list", []):
                 _add_to_ledger(item["phone"], "apify_official", item["url"], points=50)
             
             # Handle Emails with filtering
             SCRAPER_SERVICE_DOMAINS = ["fnshift", "apify", "practicaltools"]
             raw_emails = list(apify_contacts.get("emails", set()))
             final_emails = [e for e in raw_emails if not any(s in e.lower() for s in SCRAPER_SERVICE_DOMAINS)]
             if final_emails:
                 lead.email = ", ".join(list(dict.fromkeys(final_emails))[:3])
             
             # Socials
             l_inks = list(apify_contacts.get("linkedIns", set()))
             if l_inks: lead.linkedin_url = l_inks[0]
             
             i_inks = list(apify_contacts.get("instagrams", set()))
             if i_inks: lead.instagram_url = i_inks[0]
             
             t_inks = list(apify_contacts.get("twitters", set()))
             if t_inks: lead.twitter_url = t_inks[0]
             
             f_inks = list(apify_contacts.get("facebooks", set()))
             if f_inks: lead.facebook_url = f_inks[0]
             
             # Update Website URL
             if not lead.website_url:
                 lead.website_url = start_urls[0]["url"]

    # STEP 3: Consensus Voting & Selection
    
    # Apply Toll-Free Penalty
    for norm, data in phone_ledger.items():
        if norm.startswith("1800") or norm.startswith("1860") or norm.startswith("800"):
             data["score"] -= 500
             logger.info(f"[{lead.name}] Applied toll-free penalty to: {norm}")
             
    # Sort by score descending
    sorted_ledger = sorted(phone_ledger.items(), key=lambda x: x[1]["score"], reverse=True)
    
    if sorted_ledger:
        # Take up to 4 top unique numbers
        top_entries = sorted_ledger[:4]
        
        # Reconstruct the display string using the first raw format observed for the top numbers
        lead.phone = ", ".join([list(data["raw_formats"])[0] for norm, data in top_entries])
        
        if not lead.ai_research_insights:
            lead.ai_research_insights = {}
            
        # Store full consensus metadata for frontend
        consensus_data = {}
        for norm, data in top_entries:
            display_raw = list(data["raw_formats"])[0]
            consensus_data[display_raw] = {
                "score": data["score"],
                "sources": data["sources"]
            }
            
        lead.ai_research_insights["phone_consensus"] = consensus_data
        logger.info(f"[{lead.name}] Result for precise scrape: {lead.phone}")
    else:
        logger.info(f"[{lead.name}] No precise phone numbers found.")

    # ── STEP 4: LAST-RESORT DIRECTORY FALLBACK ──
    # Fires ONLY when: no phone found AND lead has no official website.
    # Uses JustDial / IndiaMart / Sulekha snippets (not full crawl) as the
    # final safety net for companies with zero digital presence.
    no_phone_yet = not lead.phone or not lead.phone.strip()
    no_website = not lead.website_url or not lead.website_url.strip()

    if no_phone_yet and no_website and lead.state:
        directory_phones = await _get_directory_phone_fallback(lead)
        for item in directory_phones:
            _add_to_ledger(item["phone"], "directory_fallback", item["url"], points=5)

        # Re-run consensus selection if new phones were found
        if directory_phones:
            sorted_ledger = sorted(phone_ledger.items(), key=lambda x: x[1]["score"], reverse=True)
            if sorted_ledger:
                top_entries = sorted_ledger[:4]
                lead.phone = ", ".join([list(data["raw_formats"])[0] for norm, data in top_entries])
                if not lead.ai_research_insights:
                    lead.ai_research_insights = {}
                consensus_data = {}
                for norm, data in top_entries:
                    display_raw = list(data["raw_formats"])[0]
                    consensus_data[display_raw] = {"score": data["score"], "sources": data["sources"]}
                lead.ai_research_insights["phone_consensus"] = consensus_data
                logger.info(f"[{lead.name}] Directory Fallback final result: {lead.phone}")

    return lead

async def deep_enrich_from_website(lead: LeadProfile, website_url: str) -> LeadProfile:
    """
    DISCOVERY MODE BRIDGE:
    Targeted Apify run on a known website to extract Socials and Phones.
    Does NOT use Tavily Search or Consensus Engine.
    """
    if not website_url or _classify_url(website_url, lead) < 0:
        return lead
        
    logger.info(f"[{lead.name}] Discovery Bridge: Triggering targeted Apify on {website_url}")
    
    start_urls = [{"url": website_url}]
    # Run a faster, targeted scrape (max 4 requests)
    contacts = await asyncio.to_thread(_run_apify_contact_actor_sync, start_urls, lead.name, 4)
    
    if not contacts:
        return lead
        
    # 1. Update Socials if missing
    if not lead.instagram_url and contacts.get("instagrams"):
        lead.instagram_url = list(contacts["instagrams"])[0]
    if not lead.facebook_url and contacts.get("facebooks"):
        lead.facebook_url = list(contacts["facebooks"])[0]
    if not lead.linkedin_url and contacts.get("linkedIns"):
        lead.linkedin_url = list(contacts["linkedIns"])[0]
    if not lead.twitter_url and contacts.get("twitters"):
        lead.twitter_url = list(contacts["twitters"])[0]
        
    # 2. Add highly reliable phone numbers from the official site
    phone_found = False
    for p in contacts.get("phones", set()):
        norm = _normalize_phone(p)
        if len(norm) >= 10 and not (norm.startswith("1800") or norm.startswith("1860")):
            # If lead.phone is empty or currently only has the Google Maps number, 
            # we prepend the official website number as high-priority
            current_phones = [ph.strip() for ph in lead.phone.split(",")] if lead.phone else []
            if p not in current_phones:
                current_phones.insert(0, p)
                lead.phone = ", ".join(current_phones[:3])
                phone_found = True
                
    if phone_found:
        logger.info(f"[{lead.name}] Discovery Bridge found phone on site: {lead.phone}")
        
    return lead
