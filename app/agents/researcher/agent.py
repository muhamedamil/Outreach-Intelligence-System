# app/agents/researcher/agent.py

from typing import List

from app.agents.researcher.tools import search_tavily
from app.agents.researcher.prompt import build_prompt
from app.agents.researcher.parser import safe_parse_json

from app.services.scraper.service import scrape_multiple
from app.agents.contact_finder.sources import build_directory_urls
from app.services.llm.client import llm_generate
from app.models.business import SizeSignals, DigitalPresence, ToolsDetected, BusinessProfile, Source
import logging

logger = logging.getLogger(__name__)

# SOURCE RELIABILITY
def get_source_reliability(url: str) -> float:
    if "linkedin.com" in url:
        return 0.6
    elif "justdial" in url or "indiamart" in url:
        return 0.7
    return 0.9  # assume official / high-trust


# CONTEXT BUILDER
def build_context(scraped: List[dict], max_sources: int = 3) -> str:
    chunks = []

    for s in scraped[:max_sources]:
        content = s.get("content", "")
        if content:
            chunks.append(content[:1500])

    return "\n\n".join(chunks)


# NORMALIZATION (CRITICAL)
def normalize_output(data: dict) -> dict:
    def to_str(val):
        if val is None:
            return None
        if isinstance(val, (str, int, float)):
            return str(val)
        return str(val)

    def is_valid_url(url: str) -> bool:
        if not url or not isinstance(url, str):
            return False
        # Basic check for http/https to satisfy Pydantic HttpUrl
        return url.lower().startswith(("http://", "https://"))

    # Website validation
    website = data.get("website")
    if not is_valid_url(website):
        website = None

    # Social links validation & filtering
    raw_social = data.get("social_links", [])
    if not isinstance(raw_social, list):
        raw_social = [raw_social] if raw_social else []
    
    social_links = [
        str(link) for link in raw_social 
        if is_valid_url(link)
    ]

    return {
        "industry": to_str(data.get("industry")),
        "description": to_str(data.get("description")),
        "employee_estimate": to_str(data.get("employee_estimate")),
        "branches": to_str(data.get("branches")),
        "website": website,
        "social_links": social_links,
        "booking_system": to_str(data.get("booking_system")),
        "crm": to_str(data.get("crm")),
        "communication": to_str(data.get("communication")),
    }


# CONFIDENCE SCORING
def compute_confidence(data: dict, sources: int) -> float:
    score = 0.0

    if data.get("description"):
        score += 0.2
    if data.get("industry"):
        score += 0.2
    if data.get("website"):
        score += 0.2
    if data.get("employee_estimate"):
        score += 0.2
    if sources >= 3:
        score += 0.2

    return round(min(score, 1.0), 2)


# -------------------------
# MAIN AGENT
# -------------------------
async def run_researcher(input_data: dict) -> BusinessProfile:
    logger.info(f"--- RESEARCHER START ---")
    logger.info(f"Input: {input_data}")

    company = input_data.get("company_name")
    location = input_data.get("location")

    # VALIDATION
    if not company:
        return BusinessProfile(
            company_name="unknown",
            location=location or "",
            confidence_score=0.0,
            sources=[],
        )

    query = f"{company} {location} business details"

    # SEARCH
    logger.info(f"Searching Tavily for: '{query}'")
    search_results = await search_tavily(query, include_raw=False)
    
    # Extract URLs
    urls = [r["url"] for r in search_results]
    
    # FALLBACK to manual search if Tavily failed
    if not urls:
        logger.warning("Tavily search yielded no URLs. Falling back to directory search...")
        urls = build_directory_urls(company, location)
    
    urls = list(dict.fromkeys(urls))
    logger.info(f"Unique URLs found: {len(urls)} -> {urls}")

    if not urls:
        return BusinessProfile(
            company_name=company, location=location, confidence_score=0.0, sources=[]
        )

    # SCRAPE (LIMITED + ASYNC)
    logger.info(f"Scraping URLs...")
    scraped = await scrape_multiple(urls)

    # MERGE TAVILY CONTENT IF SCRAPE FAILED
    # If a URL failed to scrape but Tavily has content, use Tavily's version
    logger.info(f"Checking merge for {len(search_results)} Tavily results...")
    for result in search_results:
        url = result["url"]
        content = result.get("content", "")
        
        # Check if we already have a successful scrape for this URL
        is_already_scraped = any(s["url"] == url and s.get("success") for s in scraped)
        
        if not is_already_scraped and content:
            logger.info(f"Fallback: Adding Tavily content for {url} ({len(content)} chars)")
            scraped.append({
                "url": url,
                "content": content,
                "success": True,
                "source": "tavily"
            })
        elif is_already_scraped:
             logger.debug(f"URL already successfuly scraped: {url}")
        else:
             logger.debug(f"No usable content from Tavily for: {url}")

    scraped = sorted(scraped, key=lambda x: len(x.get("content", "")), reverse=True)
    logger.info(f"Total usable contents: {len(scraped)}")

    if not scraped:
        return BusinessProfile(
            company_name=company, location=location, confidence_score=0.1, sources=[]
        )

    # CONTEXT BUILD
    context = build_context(scraped)

    if not context.strip():
        return BusinessProfile(
            company_name=company,
            location=location,
            confidence_score=0.2,
            sources=[
                Source(
                    type="web",
                    url=s["url"],
                    reliability=get_source_reliability(s["url"]),
                )
                for s in scraped
            ],
        )

    # LLM SYNTHESIS
    logger.info(f"Building summary for LLM context (length: {len(context)})")
    prompt = build_prompt(context, company, location)

    logger.info("Calling LLM to extract business details...")
    llm_output = await llm_generate(prompt)
    logger.debug(f"LLM Output: {llm_output}")

    if not llm_output:
        return BusinessProfile(
            company_name=company,
            location=location,
            confidence_score=0.2,
            sources=[
                Source(
                    type="web",
                    url=s["url"],
                    reliability=get_source_reliability(s["url"]),
                )
                for s in scraped
            ],
        )

    parsed = safe_parse_json(llm_output)
    normalized = normalize_output(parsed)

    # SOURCE OBJECTS
    sources = [
        Source(type="web", url=s["url"], reliability=get_source_reliability(s["url"]))
        for s in scraped
    ]

    # CONFIDENCE
    confidence = compute_confidence(normalized, len(scraped))
    logger.info(f"Researcher confidence formulated: {confidence}")
    logger.info(f"--- RESEARCHER END ---")

    # -------------------------
    # FINAL OBJECT
    # -------------------------
    return BusinessProfile(
        company_name=company,
        location=location,
        industry=normalized.get("industry"),
        description=normalized.get("description"),
        size_signals=SizeSignals(
            employee_estimate=normalized.get("employee_estimate"),
            branches=normalized.get("branches"),
        ),
        digital_presence=DigitalPresence(
            website=normalized.get("website"),
            social_links=normalized.get("social_links", []),
        ),
        tools_detected=ToolsDetected(
            booking_system=normalized.get("booking_system"),
            crm=normalized.get("crm"),
            communication=normalized.get("communication"),
        ),
        sources=sources,
        confidence_score=confidence,
    )
