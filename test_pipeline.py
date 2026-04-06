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
    
    print("\n--- TESTING LEAD: India Commercial Services (Consensus Engine) ---")
    enriched_lead = await run_multisite_contact_scraper(lead)
    
    print("\n[RESULT]")
    print(f"Name: {enriched_lead.name}")
    print(f"Phone (Selected): {enriched_lead.phone}")
    consensus = enriched_lead.ai_research_insights.get('phone_consensus', {})
    for num, data in consensus.items():
        print(f" -> {num} | Score: {data['score']} | Sources: {[s.get('url') or s.get('type') for s in data['sources']]}")
    
    if consensus:
        print("✅ SUCCESS: Phone consensus engine computed.")
    else:
        print("❌ ERROR: No phone consensus captured.")

if __name__ == "__main__":
    asyncio.run(test_traceability())
