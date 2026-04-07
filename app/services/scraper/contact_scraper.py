# app/services/scraper/contact_scraper.py
import logging
import asyncio
import re
from typing import List, Dict, Any, Optional
import httpx

from apify_client import ApifyClient
from app.models.lead import LeadProfile
from app.config.settings import settings

logger = logging.getLogger(__name__)

# BLACKLIST for StartURLs: Sites that "trap" the crawler, hide data, or give generic corporate info.
STARTURL_BLACKLIST = [
    'linkedin.com', 'dnb.com', 'zoominfo.com', 'crunchbase.com', 
    'apollo.io', 'indiamart.com', 'justdial.com',
     'instagram.com', 'twitter.com', 'youtube.com',
    'tatamotors.com', 'mahindra.com', 'ashokleyland.com',
    'zaubacorp.com', 'tracxn.com', 'tofler.in', 'economictimes.indiatimes.com',
    'tradeindia.com', 'exportersindia.com', 'enfsolar.com', 'solaralmanac.info',
    'indialog.com', 'dir.indiamart.com'
]

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
                    "include_answer": True # CRITICAL: Get AI-summarized phone
                },
                timeout=15.0
            )
            
            if res.status_code == 200:
                data = res.json()
                ai_answer = data.get("answer")
                
                # EXTRACT FROM SNIPPETS WITH PROXIMITY
                name_words = lead.name.lower().split()[:2] # Key words to search for
                
                for result in data.get("results", []):
                    url = result.get("url", "").lower()
                    content = result.get("content", "")
                    content_lower = content.lower()
                    
                    # PROXIMITY CHECK: Only extract phone if name or state is mentioned near it
                    for match in PHONE_REGEX.finditer(content):
                        phone_val = match.group().strip()
                        pos = match.start()
                        
                        # Look at context around the phone number
                        context = content_lower[max(0, pos-80) : min(len(content_lower), pos+80)]
                        
                        # If lead name or state is in the direct context, it's a high-confidence local number
                        if any(word in context for word in name_words) or (lead.state and lead.state.lower() in context):
                             snippet_phones.append({
                                 "phone": phone_val,
                                 "url": result.get("url")
                             })
                    
                    # FILTER: Select high-quality StartURLs for deep crawl
                    if url and not any(b in url for b in STARTURL_BLACKLIST):
                        start_urls.append({"url": result["url"]})
                        
                start_urls = start_urls[:2]
            else:
                logger.error(f"[{lead.name}] Tavily Search failed status {res.status_code}")
    except Exception as e:
        logger.error(f"[{lead.name}] Tavily Precision Search failed: {e}")
        
    return start_urls, snippet_phones, ai_answer

def _run_apify_contact_actor_sync(start_urls: List[Dict[str, str]], lead_name: str, max_requests: int = 7) -> Dict[str, Any]:
    """Runs the Apify client and parses results with source URLs."""
    if not start_urls: return {}
    
    client = ApifyClient(settings.APIFY_TOKEN)
    run_input = {
        "startUrls": start_urls,
        "maxDepth": 3, 
        "maxRequests": max_requests, # Flexible limit for speed/cost balance
        "stayWithinDomain": True,
        "extractFromText": True,
        "includeScriptContent": True
    }
    
    logger.info(f"[{lead_name}] Launching vdrmota/contact-info-scraper on precise URL: {start_urls[0]['url']}")
    
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
    
    # STEP 1: AI Discovery + High-Confidence Snippets
    start_urls, tavily_snippet_phones, ai_answer = await _get_precision_contacts_via_tavily(lead)
    
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

    return lead

async def deep_enrich_from_website(lead: LeadProfile, website_url: str) -> LeadProfile:
    """
    DISCOVERY MODE BRIDGE:
    Targeted Apify run on a known website to extract Socials and Phones.
    Does NOT use Tavily Search or Consensus Engine.
    """
    if not website_url or any(b in website_url.lower() for b in STARTURL_BLACKLIST):
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
