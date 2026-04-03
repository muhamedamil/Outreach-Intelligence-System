import asyncio
import logging
import json
import sys
from typing import List, Dict, Any

# CRITICAL WINDOWS FIX: 
# Playwright and subprocesses require ProactorEventLoop on Windows
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from app.services.campaign.campaign_manager import run_full_campaign
from app.models.lead import LeadCategory

# Set logging to INFO to see the pipeline's progress
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

def print_lead_report(results: List[Dict[str, Any]]):
    if not results:
        return

    logger.info("\n" + "="*60)
    logger.info("🔍 Phase 1 & 2: Detailed Lead Intelligence Report")
    logger.info("="*60)

    stats = {
        "static": 0,
        "no_website": 0,
        "automated": 0,
        "whatsapp": 0,
        "social": 0
    }

    for res in results:
        lead = res["lead"]
        outreach = res["campaign_outreach"]
        
        # Emoji and Category Mapping
        cat = lead.get("category", "NO_WEBSITE")
        cat_str = cat
        emoji = "⚪"
        if cat == LeadCategory.STATIC_WEBSITE:
            emoji = "🟡"
            stats["static"] += 1
        elif cat == LeadCategory.NO_WEBSITE:
            emoji = "🔴"
            stats["no_website"] += 1
        elif cat == LeadCategory.FULLY_AUTOMATED:
            emoji = "🔵"
            stats["automated"] += 1

        if lead.get("whatsapp_status") == "DETECTED":
            stats["whatsapp"] += 1
        
        if any([lead.get("instagram_url"), lead.get("facebook_url"), lead.get("tiktok_url"), lead.get("yelp_url")]):
            stats["social"] += 1

        logger.info(f"\n  {emoji} {lead['name']}")
        logger.info(f"     Category:     {cat_str}")
        logger.info(f"     Website:      {lead.get('website_url', 'None')}")
        logger.info(f"     Web Status:   {lead.get('website_status', 'NONE')}")
        logger.info(f"     Phone:        {lead.get('phone', 'N/A')}")
        logger.info(f"     Phone (raw):  {lead.get('phone_unformatted', 'N/A')}")
        logger.info(f"     Address:      {lead.get('address', 'N/A')}")
        logger.info(f"     Rating:       {lead.get('google_rating', '0')}⭐ ({lead.get('google_review_count', 0)} reviews)")
        logger.info(f"     Booking:      {lead.get('booking_system', 'None')}")
        logger.info(f"     WhatsApp:     {lead.get('whatsapp_status', 'NOT_FOUND')} | {lead.get('whatsapp_number', lead.get('phone', ''))}")
        logger.info(f"     Instagram:    {lead.get('instagram_url', 'None')}")
        logger.info(f"     Facebook:     {lead.get('facebook_url', 'None')}")
        logger.info(f"     TikTok:       {lead.get('tiktok_url', 'None')}")
        logger.info(f"     Yelp:         {lead.get('yelp_url', 'None')}")
        logger.info(f"     Lead Score:   {lead.get('lead_score', 0)}/100")
        
        # Format breakdown as clean JSON
        breakdown = json.dumps(lead.get('lead_score_breakdown', {}), indent=2)
        logger.info(f"     Score Detail: {breakdown}")
        
        # Show top 2 reviews if available
        reviews = lead.get("reviews", [])
        if reviews and isinstance(reviews, list):
            logger.info("     Reviews:")
            for rev in reviews[:2]:
                if not rev: continue
                stars = rev.get('stars', 5)
                # Handle cases where review text is None
                raw_text = rev.get('text') or ""
                text = raw_text[:80].replace('\n', ' ')
                logger.info(f"       - {stars}⭐ \"{text}...\"")

    logger.info("\n" + "="*60)
    logger.info("📊 FINAL RESULTS")
    logger.info("="*60)
    logger.info(f"  🟡 Static Website (TARGET):      {stats['static']}")
    logger.info(f"  🔴 No Website (HIGH POTENTIAL):   {stats['no_website']}")
    logger.info(f"  🔵 Fully Automated (SKIP):        {stats['automated']}")
    logger.info(f"  📱 WhatsApp Verified:             {stats['whatsapp']}")
    logger.info(f"  📸 Social Media Found:            {stats['social']}")
    logger.info(f"  📊 Total Leads:                   {len(results)}")
    logger.info("="*60)

async def test_prompt_mode():
    logger.info("\n🧪 --- TESTING PROMPT MODE --- 🧪")
    # New strategy: Mapping niche/ethnic salons
    prompt = "Find all Indian beauty salons in Plano, TX with poor websites"
    results = await run_full_campaign(prompt=prompt, limit=2)
    print_lead_report(results)

async def test_excel_mode():
    logger.info("\n🧪 --- TESTING EXCEL MODE (ENRICHMENT) --- 🧪")
    csv_content = b"name,city,address\nBeauty by Sadia,Dallas,TX\n"
    results = await run_full_campaign(file_content=csv_content, file_name="leads.csv", limit=1)
    print_lead_report(results)

async def test_solar_mode():
    logger.info("\n🧪 --- TESTING SOLAR DISCOVERY MODE --- 🧪")
    prompt = "Find 1 solar energy installation company in Austin TX"
    results = await run_full_campaign(prompt=prompt, limit=1)
    print_lead_report(results)

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    logger.info("Starting Pipeline Verification...")
    loop.run_until_complete(test_prompt_mode())
    loop.run_until_complete(test_excel_mode())
    loop.run_until_complete(test_solar_mode())
    logger.info("\n✅ All tests complete!")
