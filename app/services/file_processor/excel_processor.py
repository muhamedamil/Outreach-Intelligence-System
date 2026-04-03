# app/services/file_processor/excel_processor.py

import logging
from typing import List, Dict, Any
from app.services.campaign.campaign_manager import run_full_campaign

logger = logging.getLogger(__name__)

async def process_excel_content(
    file_content: bytes, 
    file_name: str, 
    limit: int = 50
) -> List[Dict[str, Any]]:
    """
    Unified entry point for Excel/CSV processing using the 
    Premium 4-Layer Intelligence Engine.
    """
    logger.info(f"Processing Excel content: {file_name}")
    
    # Delegate everything to the Universal Campaign Manager
    # This automatically handles:
    # 1. Fuzzy Column Mapping (Name, Address, etc.)
    # 2. Layer 1 (GMaps Enrichment & Review Scraping)
    # 3. Layer 2 (Website Deep-Scan and WhatsApp Detection)
    # 4. Layer 3 (OSINT Social Resolution)
    # 5. Layer 4 (AI Research hooks and Outreach Writing)
    
    results = await run_full_campaign(
        file_content=file_content,
        file_name=file_name,
        limit=limit
    )
    
    return results