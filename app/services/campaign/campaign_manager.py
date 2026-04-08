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

from app.config.settings import settings
from app.models.lead import LeadProfile
from app.services.campaign.input_parser import parse_user_input, LeadSpec
from app.services.gmaps.apify_client import search_leads, enrich_leads
from app.services.scraper.website_analyzer import analyze_lead_website
from app.services.scraper.social_resolver import resolve_missing_socials
from app.agents.researcher.agent import run_lead_researcher
from app.agents.outreach_writer.agent import generate_lead_outreach
from app.services.dedup.csv_dedup import SeenLeadsIndex, filter_new_leads

logger = logging.getLogger(__name__)


async def run_discovery_campaign(
    prompt: Optional[str] = None,
    file_content: Optional[bytes] = None,
    file_name: Optional[str] = None,
    limit: int = 10,
    seen_index: Optional[SeenLeadsIndex] = None  # CSV-based dedup index
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
        requested_limit = spec.limit if spec.limit else limit
        
        # ── DISCOVERY OVERSHOOT ──
        # If we have a list of seen leads, we must ask Apify for MORE than the limit.
        # Otherwise, if the first 10 results are all duplicates, the user gets 0 results.
        # We overshoot by 2.5x if a dedup file is present (capped at 50 to control cost).
        target_limit = requested_limit
        if seen_index and seen_index.total_seen > 0:
            target_limit = int(requested_limit * 2.5)
            logger.info(f"[Dedup] Buffering request: {requested_limit} requested -> {target_limit} search limit")

        leads = await search_leads(
            location=spec.location or "USA",
            queries=spec.queries,
            industry=spec.industry,
            max_results=target_limit
        )

        # ── DEDUP FILTER ──
        if seen_index and seen_index.total_seen > 0:
            leads, skipped_count = filter_new_leads(leads, seen_index)
            if not leads:
                logger.warning(
                    f"All {skipped_count} discovered leads were duplicates. "
                    f"Try a different search area or upload a fresh query."
                )
                return []
        
        # Ensure we only process the specific amount requested by the user
        process_limit = requested_limit
    else:
        # Bypassing Apify for Enrichment mode
        leads = spec.provided_leads
        process_limit = len(leads) # Process all leads for Excel uploads

    if not leads:
        logger.warning("No leads found in Layer 1. Pipeline stopping.")
        return []

    logger.info(f"Found {len(leads)} leads to process...")
    process_limit = min(len(leads), process_limit)
    active_leads = leads[:process_limit]

    # --- PHASE 1: LOCAL SURFACE SCANS (Concurrent) ---
    logger.info(f"🌐 [Phase 1] Starting concurrent surface scans for {len(active_leads)} leads...")
    
    semaphore = asyncio.Semaphore(settings.SCRAPER_CONCURRENCY)

    async def _surface_scan(l: LeadProfile):
        async with semaphore:
            try:
                # Fast local playwright scan
                return await analyze_lead_website(l)
            except Exception as e:
                logger.error(f"Surface scan failed for {l.name}: {e}")
                return l

    tasks_p1 = [_surface_scan(l) for l in active_leads]
    active_leads = await asyncio.gather(*tasks_p1)

    # --- PHASE 2: BATCH APIFY ENRICHMENT (Discovery Mode) ---
    if spec.mode == "DISCOVERY":
        logger.info("🧪 [Phase 2] Checking for leads requiring deep enrichment...")
        
        # Identify leads missing critical data (Instagram or Facebook)
        enrich_map: Dict[str, LeadProfile] = {}
        for l in active_leads:
            if l.website_url and (not l.instagram_url or not l.facebook_url):
                enrich_map[l.website_url] = l
        
        if enrich_map:
            from app.services.scraper.contact_scraper import batch_deep_enrich_from_websites
            # This triggers exactly ONE Apify run for the whole batch
            await batch_deep_enrich_from_websites(enrich_map)
        
        # Final social resolution safety net (local OSINT)
        async def _resolve_socials(l: LeadProfile):
            try:
                return await resolve_missing_socials(l)
            except Exception:
                return l

        tasks_p2 = [_resolve_socials(l) for l in active_leads]
        active_leads = await asyncio.gather(*tasks_p2)
    else:
        # ENRICHMENT MODE: Use multi-site scraper (retains higher specificity for single excel rows)
        # But we'll still parallelize it
        from app.services.scraper.contact_scraper import run_multisite_contact_scraper
        
        async def _deep_scrape(l: LeadProfile):
            async with semaphore:
                try:
                    l = await run_multisite_contact_scraper(l)
                    # Sync with what we do for discovery
                    l = await analyze_lead_website(l)
                    l = await resolve_missing_socials(l)
                    return l
                except Exception:
                    return l

        tasks_deep = [_deep_scrape(l) for l in active_leads]
        active_leads = await asyncio.gather( *tasks_deep)

    # --- PHASE 3: AI RESEARCH (Concurrent) ---
    logger.info(f"🧠 [Phase 3] Running AI research insights for {len(active_leads)} leads...")
    
    async def _ai_research(l: LeadProfile):
        async with semaphore:
            try:
                l = await run_lead_researcher(l)
                logger.info(f"[{l.name}] Score: {l.lead_score} | Insights: {l.ai_research_insights is not None}")
                return l
            except Exception as e:
                logger.error(f"AI research failed for {l.name}: {e}")
                return l

    tasks_p3 = [_ai_research(l) for l in active_leads]
    final_leads = await asyncio.gather(*tasks_p3)

    results = [
        {
            "lead": l.model_dump(),
            "campaign_outreach": None
        }
        for l in final_leads
    ]

    logger.info(f"DISCOVERY COMPLETE | Finalized {len(results)} leads")
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
