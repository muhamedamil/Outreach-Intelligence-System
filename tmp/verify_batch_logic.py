import asyncio
import logging
from unittest.mock import MagicMock, patch
from app.models.lead import LeadProfile
from app.services.scraper.contact_scraper import batch_deep_enrich_from_websites

logging.basicConfig(level=logging.INFO)

async def test_batch_logic():
    # 1. Setup mock leads
    lead1 = LeadProfile(name="Salon Alpha", website_url="https://alphacut.com", location="Houston")
    lead2 = LeadProfile(name="Beta Nails", website_url="https://betanails.com", location="Houston")
    
    url_map = {
        "https://alphacut.com": lead1,
        "https://betanails.com": lead2
    }

    # 2. Mock dataset items from Apify
    mock_items = [
        {
            "url": "https://alphacut.com/contact",
            "aggregatedResults": {
                "phones": ["+1 713-111-2222"],
                "instagrams": ["https://instagram.com/alphacut"]
            }
        },
        {
            "url": "https://betanails.com/about",
            "aggregatedResults": {
                "emails": ["info@betanails.com"],
                "facebooks": ["https://facebook.com/betanails"]
            }
        }
    ]

    # 3. Patch the Apify runner
    with patch("app.services.scraper.contact_scraper._run_apify_contact_actor_sync") as mock_run:
        mock_run.return_value = mock_items
        
        print("Starting batch enrichment test...")
        results = await batch_deep_enrich_from_websites(url_map)
        
        # 4. Assertions
        print(f"Lead 1 Phone: {lead1.phone}")
        print(f"Lead 1 Instagram: {lead1.instagram_url}")
        print(f"Lead 2 Email: {lead2.email}")
        print(f"Lead 2 Facebook: {lead2.facebook_url}")
        
        assert "+1 713-111-2222" in lead1.phone
        assert lead1.instagram_url == "https://instagram.com/alphacut"
        assert "info@betanails.com" in lead2.email
        assert lead2.facebook_url == "https://facebook.com/betanails"
        
        print("\n✅ Verification SUCCESS: Batch data correctly mapped back to leads.")

if __name__ == "__main__":
    asyncio.run(test_batch_logic())
