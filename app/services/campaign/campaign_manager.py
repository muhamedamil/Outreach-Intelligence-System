# app/services/campaign/campaign_manager.py
#
# The Orchestrator: The Main Engine of the Four-Layer Salon Pipeline
#
# PHASE 1 (run_discovery_campaign): Layers 1–3 — fast discovery + enrichment
# PHASE 2 (run_outreach_for_lead / run_outreach_for_batch): Layer 4 on-demand

import logging
import asyncio
import sys
from typing import List, Dict, Any, Optional

# CRITICAL WINDOWS FIX:
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


async def run_discovery_campaign(
    prompt: Optional[str] = None,
    file_content: Optional[bytes] = None,
    file_name: Optional[str] = None,
    limit: int = 10
) -> List[Dict[str, Any]]:
    """
    PHASE 1: Layers 1–3 only.
    Runs Discovery + Website Analysis + AI Research.
    Does NOT generate outreach. Returns results with outreach=None.
    """
    logger.info("--- STARTING DISCOVERY PIPELINE (Layers 1-3) ---")

    # LAYER 0: Input parsing
    spec: LeadSpec = await parse_user_input(prompt, file_content, file_name)

    # LAYER 1: Discovery or Enrichment
    leads: List[LeadProfile] = []
    if spec.mode == "DISCOVERY":
        target_limit = spec.limit if spec.limit else limit
        logger.info(f"Target limit configured: {target_limit}")
        
        leads = await search_leads(
            location=spec.location or "USA",
            queries=spec.queries,
            industry=spec.industry,
            max_results=target_limit
        )
        process_limit = target_limit
    else:
        # Bypassing Apify for Enrichment mode
        leads = spec.provided_leads
        process_limit = len(leads) # Process all leads for Excel uploads

    if not leads:
        logger.warning("No leads found in Layer 1. Pipeline stopping.")
        return []

    logger.info(f"Found {len(leads)} leads to process...")

    results = []

    for i, lead in enumerate(leads[:process_limit]):
        logger.info(f"[{i+1}/{process_limit}] Processing: {lead.name}")
        try:
            if spec.mode == "DISCOVERY":
                # LAYER 1: Surface Scan (Playwright)
                # Fast & extracts raw text context for AI
                lead = await analyze_lead_website(lead)
                
                # LAYER 2: Targeted Deep Crawl (Apify)
                # Only fire if critical data is missing (e.g. no Ig/Fb found on homepage)
                if lead.website_url and (not lead.instagram_url or not lead.facebook_url):
                    from app.services.scraper.contact_scraper import deep_enrich_from_website
                    lead = await deep_enrich_from_website(lead, lead.website_url)
                    
                # LAYER 3: OSINT Social Resolver (DDG Dorking)
                # Final safety net for missing handles
                lead = await resolve_missing_socials(lead)
            else:
                # ENRICHMENT
                from app.services.scraper.contact_scraper import run_multisite_contact_scraper, deep_enrich_from_website
                
                # Keep the existing logic for scraping the mobile number in enrichment mode
                lead = await run_multisite_contact_scraper(lead)
                
                # Add on top of that the things used in discovery mode
                lead = await analyze_lead_website(lead)
                
                if lead.website_url and (not lead.instagram_url or not lead.facebook_url):
                    lead = await deep_enrich_from_website(lead, lead.website_url)
                    
                lead = await resolve_missing_socials(lead)

            # LAYER 3: AI research (pain points, hooks, score)
            lead = await run_lead_researcher(lead)

            logger.info(f"[{lead.name}] Score: {lead.lead_score} | Insights: {lead.ai_research_insights is not None}")

            results.append({
                "lead": lead.model_dump(),
                "campaign_outreach": None   # Outreach generated on-demand in Phase 2
            })

            await asyncio.sleep(1)

        except Exception as e:
            logger.error(f"Error processing {lead.name}: {str(e)}")
            continue

    logger.info(f"DISCOVERY COMPLETE | Enriched: {len(results)} leads")
    return results


async def run_outreach_for_lead(lead_data: Dict[str, Any]) -> Dict[str, str]:
    """
    PHASE 2 (Single): Layer 4 — generate outreach for ONE lead.
    Accepts a serialized LeadProfile dict, returns outreach dict.
    """
    try:
        lead = LeadProfile(**lead_data)
    except Exception as e:
        logger.error(f"Failed to reconstruct LeadProfile: {e}")
        return {
            "whatsapp": "Could not generate outreach — invalid lead data.",
            "instagram": "Could not generate outreach — invalid lead data.",
            "selected_angle": "error"
        }

    logger.info(f"[{lead.name}] Generating outreach (on-demand)...")
    return await generate_lead_outreach(lead)


async def run_outreach_for_batch(leads_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    PHASE 2 (Batch): Layer 4 — generate outreach for MULTIPLE leads.
    Returns list of {name, outreach} dicts.
    """
    logger.info(f"Starting batch outreach generation for {len(leads_data)} leads...")
    results = []
    for i, lead_data in enumerate(leads_data):
        try:
            lead = LeadProfile(**lead_data)
            logger.info(f"[{i+1}/{len(leads_data)}] Generating outreach for {lead.name}")
            outreach = await generate_lead_outreach(lead)
            results.append({
                "name": lead.name,
                "outreach": outreach,
                "status": "success"
            })
            await asyncio.sleep(0.5)
        except Exception as e:
            logger.error(f"Batch outreach error for lead {i}: {e}")
            results.append({
                "name": lead_data.get("name", "Unknown"),
                "outreach": None,
                "status": "error"
            })
    logger.info(f"Batch outreach complete: {len(results)} processed")
    return results


# Keep backward-compatibility alias for any code that imports run_full_campaign
async def run_full_campaign(
    prompt: Optional[str] = None,
    file_content: Optional[bytes] = None,
    file_name: Optional[str] = None,
    limit: int = 10
) -> List[Dict[str, Any]]:
    """Backward-compatible alias — runs discovery only (no outreach generation)."""
    return await run_discovery_campaign(prompt, file_content, file_name, limit)
