import asyncio
import logging
from app.services.scraper.contact_scraper import run_multisite_contact_scraper
from app.models.lead import LeadProfile

logging.basicConfig(level=logging.INFO)

async def test_traceability():
    # Test lead: India Commercial Services
    lead = LeadProfile(
        name="India Commercial Services",
        state="RAJASTHAN",
        industry="Automobile Dealer"
    )
    
    print("\n--- TESTING LEAD: India Commercial Services (Traceability) ---")
    enriched_lead = await run_multisite_contact_scraper(lead)
    
    print("\n[RESULT]")
    print(f"Name: {enriched_lead.name}")
    print(f"Phone: {enriched_lead.phone}")
    print(f"Sources: {enriched_lead.ai_research_insights.get('phone_sources')}")
    
    if enriched_lead.ai_research_insights.get('phone_sources'):
        print("✅ SUCCESS: Phone sources captured.")
    else:
        print("❌ ERROR: No phone sources captured.")

if __name__ == "__main__":
    asyncio.run(test_traceability())
