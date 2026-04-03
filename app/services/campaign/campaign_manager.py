# app/services/campaign/campaign_manager.py
#
# The Orchestrator: The Main Engine of the Four-Layer Salon Pipeline
#
# Connects everything from Layer 0 (Input) to Layer 4 (Outreach).

import logging
import asyncio
import sys
from typing import List, Dict, Any, Optional

# CRITICAL WINDOWS FIX: 
# Playwright and subprocesses require ProactorEventLoop on Windows
if sys.platform == 'win32':
    try:
        from asyncio import WindowsProactorEventLoopPolicy
        if not isinstance(asyncio.get_event_loop_policy(), WindowsProactorEventLoopPolicy):
            asyncio.set_event_loop_policy(WindowsProactorEventLoopPolicy())
    except Exception:
        pass

from app.models.lead import LeadProfile
from app.services.campaign.input_parser import parse_user_input, LeadSpec
from app.services.gmaps.apify_client import search_leads, enrich_leads
from app.services.scraper.website_analyzer import analyze_lead_website
from app.services.scraper.social_resolver import resolve_missing_socials
from app.agents.researcher.agent import run_lead_researcher
from app.agents.outreach_writer.agent import generate_lead_outreach

logger = logging.getLogger(__name__)

async def run_full_campaign(
    prompt: Optional[str] = None,
    file_content: Optional[bytes] = None,
    file_name: Optional[str] = None,
    limit: int = 10
) -> List[Dict[str, Any]]:
    """
    The main execution engine for high-intelligence lead outreach.
    """
    logger.info("🚀 --- STARTING FULL FOUR-LAYER PIPELINE ---")

    # ──────────────────────────────────────
    # LAYER 0: THE GATEKEEPER (Input)
    # ──────────────────────────────────────
    spec: LeadSpec = await parse_user_input(prompt, file_content, file_name)
    
    # ──────────────────────────────────────
    # LAYER 1: THE MINER (Discovery/Enrichment)
    # ──────────────────────────────────────
    leads: List[LeadProfile] = []
    
    if spec.mode == "DISCOVERY":
        # Broad lookup by persona/location
        leads = await search_leads(
            location=spec.location or "USA",
            queries=spec.queries,
            industry=spec.industry,
            max_results=limit
        )
    else:
        # Targeted metadata lookup for the Excel list
        leads = await enrich_leads(
            leads=spec.provided_leads,
            industry=spec.industry,
            include_reviews=True
        )

    if not leads:
        logger.warning("No leads found in Layer 1. Pipeline stopping.")
        return []

    logger.info(f"📍 Found {len(leads)} leads to process...")

    results = []

    # ──────────────────────────────────────
    # LAYERS 2-4: THE REFINER, BRAIN, AND CLOSER
    # (Processed lead-by-lead for high quality)
    # ──────────────────────────────────────
    for i, lead in enumerate(leads[:limit]):
        logger.info(f"[{i+1}/{limit}] --- Processing: {lead.name} ---")
        
        try:
            # LAYER 2: THE REFINER (Deterministic Technical Check)
            # Find buckets + missing socials (WhatsApp active verification disabled)
            lead = await analyze_lead_website(lead)
            lead = await resolve_missing_socials(lead)

            # LAYER 3: THE BRAIN (AI Sentiment Hook)
            # Find pain points in reviews
            lead = await run_lead_researcher(lead)

            # LAYER 4: THE CLOSER (Multi-Channel Copy)
            # Generate the final pitches
            outreach = await generate_lead_outreach(lead)

            # Package the final intelligence object
            results.append({
                "lead": lead.model_dump(),
                "campaign_outreach": outreach
            })
            
            # Rate limit/Congestion safety
            await asyncio.sleep(1)

        except Exception as e:
            logger.error(f"Error processing {lead.name}: {str(e)}")
            continue

    logger.info(f"✅ --- PIPELINE COMPLETE | Total Enriched Results: {len(results)} ---")
    return results
