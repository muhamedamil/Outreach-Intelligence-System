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

from app.services.llm.client import llm_generate
from app.agents.researcher.parser import safe_parse_json


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
  "phone": string or null,
  "email": string or null
}}

Rules:
- Do NOT guess
- Do NOT hallucinate
- Return null if not found

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

    # VALIDATION
    if not profile:
        return build_empty_contact()

    website = None
    if profile.digital_presence:
        website = profile.digital_presence.website

    # BUILD SOURCES
    urls = build_contact_sources(
        website=website,
        company_name=profile.company_name,
        location=profile.location,
        include_directories=False  # controlled expansion
    )

    if not urls:
        return build_empty_contact()

    # SCRAPE
    scraped = await scrape_multiple(urls[:6])  # limit for performance

    if not scraped:
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

    # LLM FALLBACK (ONLY IF NEEDED)
    if not phone and not email:
        llm_data = await llm_extract_contact(combined_text)

        if llm_data:
            phone = phone or llm_data.get("phone")
            email = email or llm_data.get("email")

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

    return ContactCard(
        phone=phone,
        email=email,
        whatsapp=whatsapp,
        sources=sources,
        status=status,
        confidence_score=confidence
    )