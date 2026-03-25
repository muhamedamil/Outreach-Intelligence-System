# app/agents/researcher/agent.py

from typing import List

from app.agents.researcher.tools import search_duckduckgo
from app.agents.researcher.prompt import build_prompt
from app.agents.researcher.parser import safe_parse_json

from app.services.scraper.service import scrape_multiple
from app.services.llm.client import llm_generate
from app.models.business import SizeSignals, DigitalPresence, ToolsDetected

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
    return {
        "industry": data.get("industry"),
        "description": data.get("description"),
        "employee_estimate": data.get("employee_estimate"),
        "branches": data.get("branches"),
        "website": data.get("website"),
        "social_links": data.get("social_links") if isinstance(data.get("social_links"), list) else [],
        "booking_system": data.get("booking_system"),
        "crm": data.get("crm"),
        "communication": data.get("communication"),
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

    company = input_data.get("company_name")
    location = input_data.get("location")

    # VALIDATION
    if not company:
        return BusinessProfile(
            company_name="unknown",
            location=location or "",
            confidence_score=0.0,
            sources=[]
        )

    query = f"{company} {location} business details"

    # SEARCH
    urls = await search_duckduckgo(query)

    urls = list(dict.fromkeys(urls))

    if not urls:
        return BusinessProfile(
            company_name=company,
            location=location,
            confidence_score=0.0,
            sources=[]
        )

    # SCRAPE (LIMITED + ASYNC)
    scraped = await scrape_multiple(urls)

    scraped = sorted(scraped, key=lambda x: len(x["content"]), reverse=True)

    if not scraped:
        return BusinessProfile(
            company_name=company,
            location=location,
            confidence_score=0.1,
            sources=[]
        )

    # CONTEXT BUILD
    context = build_context(scraped)

    if not context.strip():
        return BusinessProfile(
            company_name=company,
            location=location,
            confidence_score=0.2,
            sources=[
                Source(type="web", url=s["url"], reliability=get_source_reliability(s["url"]))
                for s in scraped
            ]
        )

    # LLM SYNTHESIS
    prompt = build_prompt(context, company, location)

    llm_output = await llm_generate(prompt)

    if not llm_output:
        return BusinessProfile(
            company_name=company,
            location=location,
            confidence_score=0.2,
            sources=[
                Source(type="web", url=s["url"], reliability=get_source_reliability(s["url"]))
                for s in scraped
            ]
        )

    parsed = safe_parse_json(llm_output)
    normalized = normalize_output(parsed)

    # SOURCE OBJECTS
    sources = [
        Source(
            type="web",
            url=s["url"],
            reliability=get_source_reliability(s["url"])
        )
        for s in scraped
    ]

    # CONFIDENCE
    confidence = compute_confidence(normalized, len(scraped))

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
        branches=normalized.get("branches")
    ),

    digital_presence=DigitalPresence(
        website=normalized.get("website"),
        social_links=normalized.get("social_links", [])
    ),

    tools_detected=ToolsDetected(
        booking_system=normalized.get("booking_system"),
        crm=normalized.get("crm"),
        communication=normalized.get("communication")
    ),

    sources=sources,
    confidence_score=confidence
    )