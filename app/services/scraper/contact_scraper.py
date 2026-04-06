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
    'zaubacorp.com', 'tracxn.com', 'tofler.in', 'economictimes.indiatimes.com'
]

# Regex for detecting phone numbers
PHONE_REGEX = re.compile(r'(?:\+?91[\-\s]?)?[6789]\d{9}|\+?1?\s*[-.\s]?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}')

async def _get_precision_contacts_via_tavily(lead: LeadProfile) -> tuple[List[Dict[str, str]], Dict[str, str], Optional[str]]:
    """
    Returns:
      1. startUrls for Apify.
      2. phone_to_url (mapping from snippet phone to its source URL).
      3. ai_answer (direct AI extraction).
    """
    query = f"What is the contact phone number for {lead.name} in {lead.state} India? Find local branch numbers specifically."
    start_urls = []
    phone_to_url = {}
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
                             phone_to_url[phone_val] = result.get("url")
                    
                    # FILTER: Select high-quality StartURLs for deep crawl
                    if url and not any(b in url for b in STARTURL_BLACKLIST):
                        start_urls.append({"url": result["url"]})
                        
                start_urls = start_urls[:2]
            else:
                logger.error(f"[{lead.name}] Tavily Search failed status {res.status_code}")
    except Exception as e:
        logger.error(f"[{lead.name}] Tavily Precision Search failed: {e}")
        
    return start_urls, phone_to_url, ai_answer

def _run_apify_contact_actor_sync(start_urls: List[Dict[str, str]], lead_name: str) -> Dict[str, Any]:
    """Runs the Apify client and parses results with source URLs."""
    if not start_urls: return {}
    
    client = ApifyClient(settings.APIFY_TOKEN)
    run_input = {
        "startUrls": start_urls,
        "maxDepth": 3, 
        "maxRequests": 7, # CRITICAL FIX: vdrmota uses maxRequests, not maxCrawlPages
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
            "phone_to_url": {} # New mapping field
        }
        
        for item in client.dataset(dataset_id).iterate_items():
            # Individual page info
            page_url = item.get("url")
            agg = item.get("aggregatedResults", {})
            target = agg if agg else item # Handle both summary and individual page formats
            
            for p in target.get("phones", []) + target.get("phonesUncertain", []):
                contacts["phones"].add(p)
                if page_url: contacts["phone_to_url"][p] = page_url
                
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
    start_urls, tavily_phone_sources, ai_answer = await _get_precision_contacts_via_tavily(lead)
    
    total_findings = set(tavily_phone_sources.keys())
    phone_sources = {**tavily_phone_sources}
    
    # If the AI Answer has a phone number, add it
    if ai_answer:
         answers_phones = PHONE_REGEX.findall(ai_answer)
         for ap in answers_phones:
             total_findings.add(ap)
             # Source is AI Synthesis from search urls
             if start_urls: phone_sources[ap] = "AI synthesized from " + start_urls[0]["url"]
             logger.info(f"[{lead.name}] AI Answer provided phone: {ap}")
    
    # STEP 2: Deep Scrape standalone domain if found
    if start_urls:
         apify_contacts = await asyncio.to_thread(_run_apify_contact_actor_sync, start_urls, lead.name)
         if apify_contacts:
             for p in apify_contacts.get("phones", set()):
                 total_findings.add(p)
                 # Merge source URLs
                 if p in apify_contacts.get("phone_to_url", {}):
                     phone_sources[p] = apify_contacts["phone_to_url"][p]
             
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

    # STEP 3: Validate and Prioritize Local Numbers
    validated_phones = []
    
    # Keep the user's provided number at index 0
    if lead.phone and len(re.sub(r'\D', '', lead.phone)) >= 10:
        validated_phones.append(lead.phone)

    for p in total_findings:
        digits = re.sub(r'\D', '', p)
        # FILTER OUT 1800/800 TOLL FREE (Usually corporate/Tata)
        if digits.startswith("1800") or digits.startswith("1860") or digits.startswith("800"):
             logger.info(f"[{lead.name}] Deprecated toll-free/corporate number: {p}")
             continue
             
        if len(digits) >= 10 and digits != "1390001066":
            validated_phones.append(p)

    if validated_phones:
        # Final unique set capped at 4
        unique_final_phones = list(dict.fromkeys(validated_phones))[:4]
        lead.phone = ", ".join(unique_final_phones)
        
        # Store sources in insights
        if not lead.ai_research_insights:
            lead.ai_research_insights = {}
            
        final_sources = {}
        for p in unique_final_phones:
            if p in phone_sources:
                final_sources[p] = phone_sources[p]
        
        lead.ai_research_insights["phone_sources"] = final_sources
        logger.info(f"[{lead.name}] Result for precise scrape: {lead.phone}")
    else:
        logger.info(f"[{lead.name}] No precise phone numbers found.")

    return lead
