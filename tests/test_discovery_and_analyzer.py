# tests/test_discovery_and_analyzer.py
#
# Comprehensive test for the Lead Intelligence System:
#   1. Playwright Website Analyzer (edge cases + social extraction)
#   2. Apify Google Maps Discovery
#   3. End-to-End: Discovery → Analysis → WhatsApp Verification
#
# Usage: python -m tests.test_discovery_and_analyzer

import asyncio
import json
import sys
import os

# CRITICAL WINDOWS FIX: 
# Playwright and subprocesses require ProactorEventLoop on Windows
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models.lead import LeadProfile, LeadCategory, WhatsAppStatus, WebsiteStatus
from app.services.scraper.website_analyzer import analyze_lead_website, verify_whatsapp_number


def print_lead(lead: LeadProfile, detailed: bool = False):
    """Pretty-print a lead profile."""
    cat_emoji = {"STATIC_WEBSITE": "🟡", "NO_WEBSITE": "🔴", "FULLY_AUTOMATED": "🔵"}
    emoji = cat_emoji.get(lead.category.value, "⚪")
    
    print(f"  {emoji} {lead.name}")
    print(f"     Category:     {lead.category.value}")
    print(f"     Website:      {lead.website_url or 'None'}")
    print(f"     Web Status:   {lead.website_status.value}")
    print(f"     Phone:        {lead.phone or 'N/A'}")
    print(f"     Phone (raw):  {lead.phone_unformatted or 'N/A'}")
    print(f"     Address:      {lead.address or 'N/A'}")
    print(f"     Rating:       {lead.google_rating}⭐ ({lead.google_review_count} reviews)")
    print(f"     Booking:      {lead.booking_system or 'None'}")
    print(f"     WhatsApp:     {lead.whatsapp_status.value} | {lead.whatsapp_number or 'N/A'}")
    print(f"     Instagram:    {lead.instagram_url or 'None'}")
    print(f"     Facebook:     {lead.facebook_url or 'None'}")
    print(f"     TikTok:       {lead.tiktok_url or 'None'}")
    print(f"     Yelp:         {lead.yelp_url or 'None'}")
    print(f"     Lead Score:   {lead.lead_score}/100")
    
    if detailed and lead.lead_score_breakdown:
        print(f"     Score Detail: {json.dumps(lead.lead_score_breakdown, indent=2)}")
    
    if detailed and lead.reviews:
        print(f"     Reviews ({len(lead.reviews)}):")
        for r in lead.reviews[:3]:
            print(f"       - {r.stars}⭐ \"{(r.text or '')[:80]}...\"")
    print()


# ─────────────────────────────────────────────
# TEST 1: Website Analyzer Edge Cases (FREE)
# ─────────────────────────────────────────────

async def test_website_analyzer():
    print("\n" + "=" * 60)
    print("TEST 1: WEBSITE ANALYZER — EDGE CASES (FREE)")
    print("=" * 60)

    test_cases = [
        # Case A: Booking platform URL
        LeadProfile(
            name="Case A: Booking Platform URL",
            phone="+15551234567",
            website_url="https://www.vagaro.com/somesalon",
            google_review_count=50,
        ),
        # Case B: No website
        LeadProfile(
            name="Case B: No Website",
            phone="+15559876543",
            website_url=None,
            google_review_count=30,
        ),
        # Case C: Instagram as website
        LeadProfile(
            name="Case C: Instagram as Website",
            phone="+15551112222",
            website_url="https://www.instagram.com/beautysalon",
            google_review_count=45,
        ),
        # Case D: DEAD domain (DNS failure)
        LeadProfile(
            name="Case D: Dead Domain",
            phone="+15553334444",
            website_url="https://www.thissalondomaindoesnotexist12345.com",
            google_review_count=35,
        ),
        # Case E: Real static site (example.com)
        LeadProfile(
            name="Case E: Static Site",
            phone="+15555556666",
            website_url="https://example.com",
            google_review_count=40,
        ),
        # Case F: Already classified by Apify
        LeadProfile(
            name="Case F: Pre-classified by Apify",
            phone="+15557778888",
            website_url="https://somesite.com",
            google_review_count=60,
            category=LeadCategory.FULLY_AUTOMATED,
            booking_system="Mindbody",
        ),
    ]

    for i, lead in enumerate(test_cases):
        print(f"\n--- {lead.name} ---")
        result = await analyze_lead_website(lead)
        print_lead(result, detailed=True)

    print("✅ Website Analyzer edge case test complete!\n")


# ─────────────────────────────────────────────
# TEST 2: Apify Discovery
# ─────────────────────────────────────────────

async def test_apify_discovery():
    print("\n" + "=" * 60)
    print("TEST 2: APIFY GOOGLE MAPS DISCOVERY")
    print("=" * 60)

    from app.services.gmaps.apify_client import search_leads
    from app.config.settings import settings

    location = "Dallas, TX"
    queries = ["Indian beauty salon"]
    
    original_min = settings.REVIEW_MIN
    settings.REVIEW_MIN = 5  # Lower for testing

    print(f"  Location:  {location}")
    print(f"  Query:     {queries}")
    print(f"  Review filter: {settings.REVIEW_MIN}-{settings.REVIEW_MAX}")
    print(f"  Searching... (30-90 seconds)\n")

    try:
        leads = await search_leads(location=location, queries=queries, max_results=10)

        if not leads:
            print("⚠️  No leads found.")
        else:
            print(f"✅ Found {len(leads)} leads!\n")
            for l in leads:
                print_lead(l, detailed=True)

        settings.REVIEW_MIN = original_min
        return leads

    except Exception as e:
        print(f"\n❌ Apify test failed: {str(e)}")
        import traceback
        traceback.print_exc()
        settings.REVIEW_MIN = original_min
        return []


# ─────────────────────────────────────────────
# TEST 3: End-to-End + WhatsApp Verification
# ─────────────────────────────────────────────

async def test_end_to_end(leads: list = None):
    print("\n" + "=" * 60)
    print("TEST 3: END-TO-END (Discovery → Analysis → WhatsApp)")
    print("=" * 60)

    if not leads:
        from app.services.gmaps.apify_client import search_leads
        from app.config.settings import settings
        settings.REVIEW_MIN = 5
        leads = await search_leads(location="Dallas, TX", queries=["Indian beauty salon"], max_results=10)

    if not leads:
        print("⚠️  No leads. Skipping E2E test.")
        return

    print(f"\n🔍 Phase 1: Analyzing {len(leads)} lead websites...\n")

    results = {"STATIC_WEBSITE": 0, "NO_WEBSITE": 0, "FULLY_AUTOMATED": 0}
    whatsapp_found = 0
    social_found = 0

    for lead in leads:
        print(f"─── Analyzing: {lead.name} ───")
        
        # Step 1: Website analysis
        enriched = await analyze_lead_website(lead)
        results[enriched.category.value] += 1
        
        if enriched.instagram_url or enriched.facebook_url or enriched.tiktok_url:
            social_found += 1

    # Step 2: WhatsApp verification for all leads
    print(f"\n📱 Phase 2: Verifying WhatsApp for {len(leads)} phone numbers...\n")
    
    for lead in leads:
        if lead.whatsapp_status != WhatsAppStatus.DETECTED:
            await verify_whatsapp_number(lead)
            await asyncio.sleep(2)  # Rate limiting
        
        if lead.whatsapp_status == WhatsAppStatus.DETECTED:
            whatsapp_found += 1
        
        print_lead(lead, detailed=True)

    print("=" * 60)
    print("📊 FINAL RESULTS")
    print("=" * 60)
    print(f"  🟡 Static Website (TARGET):      {results['STATIC_WEBSITE']}")
    print(f"  🔴 No Website (HIGH POTENTIAL):   {results['NO_WEBSITE']}")
    print(f"  🔵 Fully Automated (SKIP):        {results['FULLY_AUTOMATED']}")
    print(f"  📱 WhatsApp Verified:             {whatsapp_found}")
    print(f"  📸 Social Media Found:            {social_found}")
    print(f"  📊 Total Leads:                   {len(leads)}")
    print()
    print("✅ End-to-End test complete!")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

async def main():
    print("\n🧪 UNIVERSAL LEAD INTELLIGENCE SYSTEM — FULL TEST SUITE")
    print("=" * 60)

    print("\n[1/3] Website Analyzer edge cases (FREE)...")
    await test_website_analyzer()

    print("-" * 60)
    run_apify = input("Run Apify + E2E tests? (costs ~$0.02-0.05) [y/N]: ").strip().lower()

    if run_apify == "y":
        print("\n[2/3] Apify Discovery...")
        leads = await test_apify_discovery()

        if leads:
            run_e2e = input("\nRun E2E + WhatsApp verification? [y/N]: ").strip().lower()
            if run_e2e == "y":
                print("\n[3/3] End-to-End + WhatsApp...")
                await test_end_to_end(leads)
    else:
        print("Skipping paid tests.")

    print("\n🏁 ALL TESTS COMPLETE!")


if __name__ == "__main__":
    asyncio.run(main())
