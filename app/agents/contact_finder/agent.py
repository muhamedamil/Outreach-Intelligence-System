# app/agents/contact_finder/agent.py

from typing import List, Optional

from app.models.contact import ContactCard, ContactSource, ContactStatus
from app.models.business import BusinessProfile

from app.services.scraper.service import scrape_multiple

from app.agents.contact_finder.extractor import (
    extract_phone,
    extract_email,
    extract_whatsapp
)
from app.agents.contact_finder.sources import build_contact_sources
from app.agents.contact_finder.validator import validate_contact_data
from app.agents.researcher.tools import search_tavily

from app.services.llm.client import llm_generate
from app.agents.researcher.parser import safe_parse_json

import logging

logger = logging.getLogger(__name__)


# LLM FALLBACK
async def llm_extract_contact(context: str) -> dict:
    """
    LLM fallback for extracting contact info when regex fails.
    Strictly limited to avoid hallucination.
    """

    prompt = f"""
    You are a contact extraction system.

    Extract ONLY if explicitly present in text.

    Return STRICT JSON:
    {{
      "phone": "actual_digits_only_or_null",
      "email": "actual_email_only_or_null"
    }}

    Rules:
    - Do NOT guess
    - Do NOT hallucinate
    - Do NOT return placeholders like "Click to view" or "Revealed in profile"
    - If you see "91-XXXXXXXXXX", return the full number.
    - If you see an email like "info at domain dot com", clean it into "info@domain.com"
    - Return null if no actual data is present.

    Text:
    {context}
    """

    output = await llm_generate(prompt)

    if not output:
        return {}

    parsed = safe_parse_json(output)

    if not isinstance(parsed, dict):
        return {}

    return parsed


# SAFE CONTACT OBJECT
def build_empty_contact() -> ContactCard:
    return ContactCard(
        phone=None,
        email=None,
        whatsapp=None,
        sources=[],
        status=ContactStatus.NOT_FOUND,
        confidence_score=0.0
    )


# -------------------------
# MAIN AGENT
# -------------------------
async def run_contact_finder(
    profile: BusinessProfile
) -> ContactCard:
    logger.info("--- CONTACT FINDER START ---")

    # VALIDATION
    if not profile:
        logger.warning("Contact finder received empty profile, aborting.")
        return build_empty_contact()

    website = None
    if profile.digital_presence:
        website = profile.digital_presence.website

    # BUILD SOURCES
    urls = build_contact_sources(
        website=website,
        company_name=profile.company_name,
        location=profile.location,
        include_directories=True  # ENABLE directory search
    )

    # Scrape what we found (if any)
    scraped = []
    if urls:
        logger.info(f"Contact finder scanning {len(urls[:6])} URLs...")
        scraped = await scrape_multiple(urls[:6])
    else:
        logger.info("No initial contact URLs found (website/directories).")

    # FALLBACK TO TAVILY IF NO CONTENT OR NO WEBSITE
    if not scraped or len(str(website or "")) < 3:
        logger.info(f"Website-based search insufficient. Triggering Tavily contact search for: {profile.company_name}")
        tavily_query = f"{profile.company_name} {profile.location} official contact phone number email address"
        
        # USE ADVANCED DEPTH + ANSWER for contact info
        tavily_results = await search_tavily(
            tavily_query, 
            max_results=5, 
            search_depth="advanced"
        )
        
        for tr in tavily_results:
            # Add the search 'answer' if Tavily provided one - it's usually high quality
            if tr.get("answer"):
                scraped.append({
                    "url": tr["url"],
                    "content": f"SUMMARY: {tr['answer']}\n\nRAW: {tr.get('content', '')}",
                    "success": True,
                    "source": "tavily"
                })
            else:
                scraped.append({
                    "url": tr["url"],
                    "content": tr.get("content", ""),
                    "success": True,
                    "source": "tavily"
                })

    if not scraped:
        logger.warning("Contact finder scraping returned empty results.")
        return build_empty_contact()

    # COMBINE TEXT
    combined_text = " ".join(
        s.get("content", "") for s in scraped if s.get("content")
    )

    if not combined_text.strip():
        return build_empty_contact()

    # DETERMINISTIC EXTRACTION
    phone: Optional[str] = extract_phone(combined_text)
    email: Optional[str] = extract_email(combined_text)
    logger.info(f"Regex extraction found -> Phone: {phone}, Email: {email}")

    # LLM FALLBACK (ONLY IF NEEDED)
    if not phone and not email:
        logger.info("Regex found nothing, triggering LLM fallback...")
        llm_data = await llm_extract_contact(combined_text)

        if llm_data:
            phone = phone or llm_data.get("phone")
            email = email or llm_data.get("email")
            logger.info(f"LLM fallback provided -> Phone: {phone}, Email: {email}")

    # SOURCE OBJECTS
    sources: List[ContactSource] = [
        ContactSource(type="website", url=s["url"])
        for s in scraped
    ]

    # FINALIZATION
    phone, email, status, confidence = validate_contact_data(
        phone=phone,
        email=email,
        sources_count=len(sources)
    )

    whatsapp = extract_whatsapp(phone)

    logger.info(f"--- CONTACT FINDER END | Status: {status} ---")
    return ContactCard(
        phone=phone,
        email=email,
        whatsapp=whatsapp,
        sources=sources,
        status=status,
        confidence_score=confidence
    )