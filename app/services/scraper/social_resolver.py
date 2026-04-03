# app/services/scraper/social_resolver.py
#
# OPEN FRAMEWORK SOCIAL MEDIA RESOLVER (LAYER 2 FALLBACK)
# Uses Python's native DuckDuckGo search library to perform targeted OSINT.
# No paid APIs, completely open framework.

import asyncio
import logging
import re
from typing import Optional, Tuple, List, Dict
from ddgs import DDGS
from app.models.lead import LeadProfile

logger = logging.getLogger(__name__)

# Keywords we expect in a snippet to confirm it's a beauty business or solar
CONFIDENCE_KEYWORDS = {
    "salon": ["salon", "beauty", "hair", "spa", "threading", "brows", "lashes", "nails", "aesthetics", "skincare", "barber", "studio", "makeup"],
    "solar": ["solar", "energy", "sun", "panel", "contractor", "installation", "renewable"]
}

# aggregator URLs we want to skip (e.g. hashtags, places, locations)
IGNORE_URL_PATTERNS = [
    r"instagram\.com/explore/",
    r"instagram\.com/p/",
    r"instagram\.com/reel/",
    r"instagram\.com/stories/",
    r"facebook\.com/pages/",
    r"facebook\.com/places/",
    r"facebook\.com/hashtag/",
    r"facebook\.com/events/",
    r"tiktok\.com/tag/",
]

async def resolve_missing_socials(lead: LeadProfile) -> LeadProfile:
    """
    Called when the Playwright website scan fails to find Instagram/Facebook.
    Runs sequential targeted search dorks using DuckDuckGo.
    
    Uses asyncio.to_thread to run the synchronous DDGS library without blocking.
    """
    missing_ig = not lead.instagram_url
    missing_fb = not lead.facebook_url

    if not missing_ig and not missing_fb:
        return lead

    logger.info(f"[{lead.name}] Triggering OSINT Social Resolver Fallback...")

    # ── QUERY 1: THE PHONE NUMBER DORK (Highest Accuracy) ──
    if lead.phone_unformatted and (missing_ig or missing_fb):
        clean_phone = lead.phone_unformatted.replace("+1", "").strip()
        q_phone = f'"{clean_phone}" OR "{clean_phone[:3]}-{clean_phone[3:6]}-{clean_phone[6:]}" site:instagram.com OR site:facebook.com'
        found_ig, found_fb = await _execute_dork(q_phone, lead)
        
        if missing_ig and found_ig:
            lead.instagram_url = found_ig
            lead.lead_score_breakdown["ig_osint_phone"] = 5
            lead.lead_score += 5
            missing_ig = False
            logger.info(f"[{lead.name}] Found IG via OSINT (Phone): {found_ig}")
            
        if missing_fb and found_fb:
            lead.facebook_url = found_fb
            missing_fb = False
            logger.info(f"[{lead.name}] Found FB via OSINT (Phone): {found_fb}")

    await asyncio.sleep(1.5)

    # ── QUERY 2: THE NAME + CITY DORK (INSTAGRAM) ──
    if missing_ig:
        city = lead.city or "USA"
        q_ig = f'"{lead.name}" "{city}" site:instagram.com'
        found_ig, _ = await _execute_dork(q_ig, lead)
        
        if found_ig:
            lead.instagram_url = found_ig
            lead.lead_score_breakdown["ig_osint_name"] = 5
            lead.lead_score += 5
            missing_ig = False
            logger.info(f"[{lead.name}] Found IG via OSINT (Name): {found_ig}")

    await asyncio.sleep(1.5)

    # ── QUERY 3: THE NAME + CITY DORK (FACEBOOK) ──
    if missing_fb:
        city = lead.city or "USA"
        q_fb = f'"{lead.name}" "{city}" site:facebook.com'
        _, found_fb = await _execute_dork(q_fb, lead)
        
        if found_fb:
            lead.facebook_url = found_fb
            missing_fb = False
            logger.info(f"[{lead.name}] Found FB via OSINT (Name): {found_fb}")

    lead.lead_score = min(100, lead.lead_score)
    return lead


async def _execute_dork(query: str, lead: LeadProfile) -> Tuple[Optional[str], Optional[str]]:
    """
    Executes a duckduckgo query in a separate thread and validates results.
    """
    found_ig = None
    found_fb = None
    
    def _search():
        try:
            with DDGS() as ddgs:
                return list(ddgs.text(query, max_results=5))
        except Exception as e:
            # Shift to debug to avoid log bloat during rate limits
            logger.debug(f"DDGS Search failed for [{query}]: {str(e)}")
            return []

    try:
        # Run sync search in a thread to keep async loop alive
        results = await asyncio.to_thread(_search)
        
        for res in results:
            url = res.get("href", "").lower()
            snippet = (res.get("body", "") + " " + res.get("title", "")).lower()
            
            # 1. Reject aggregator links
            if any(re.search(pat, url) for pat in IGNORE_URL_PATTERNS):
                continue
                
            # 2. Confidence signals
            city_match = lead.city and lead.city.lower() in snippet
            area_code_match = False
            if lead.phone_unformatted and len(lead.phone_unformatted) >= 10:
                area_code = lead.phone_unformatted[-10:-7]
                if area_code in snippet:
                    area_code_match = True
            
            industry_kw = CONFIDENCE_KEYWORDS.get(lead.industry, CONFIDENCE_KEYWORDS["salon"])
            industry_match = any(kw in snippet for kw in industry_kw)
            name_slug = re.sub(r'[^a-z0-9]', '', lead.name.lower())[:8]
            name_in_url = len(name_slug) > 3 and name_slug in url
            
            is_confident = city_match or area_code_match or industry_match or name_in_url
            
            if is_confident:
                if "instagram.com" in url and not found_ig:
                    found_ig = url.split("?")[0]
                elif "facebook.com" in url and not found_fb:
                    found_fb = url.split("?")[0]
                    
            if found_ig and found_fb:
                break

    except Exception as e:
        logger.warning(f"OSINT Dork execution error: {str(e)}")
        
    return found_ig, found_fb
