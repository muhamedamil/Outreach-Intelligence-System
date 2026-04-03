import asyncio
import sys
import os

# Windows Fix
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.scraper.website_analyzer import verify_whatsapp_number
from app.models.lead import LeadProfile

async def test_whatsapp_elasticity():
    print("\n🧪 Testing WhatsApp 'Elastic' Verification Logic 🧪")
    print("="*60)
    
    test_cases = [
        {
            "name": "ZR Beauty Salon (Formerly Failed)",
            "phone": "(214) 705-1100", # Google-style format used for search
            "country": "US"
        },
        {
            "name": "Standard US Salon (Success)",
            "phone": "+1 214-923-7138",
            "country": "US"
        }
    ]
    
    for case in test_cases:
        print(f"\n🚀 Testing: {case['name']}")
        lead = LeadProfile(
            name=case['name'],
            phone=case['phone'],
            country_code=case['country']
        )
        
        result = await verify_whatsapp_number(lead)
        
        print(f"   Status:      {result.whatsapp_status.value}")
        print(f"   Detected #:  {result.whatsapp_number}")
        print(f"   Breakdown:   {result.lead_score_breakdown}")
        print("-" * 40)

if __name__ == "__main__":
    asyncio.run(test_whatsapp_elasticity())
