# app/services/campaign/input_parser.py
#
# Layer 0: The Gatekeeper (Input Normalization)
#
# This service takes diverse user inputs (Excel, CSV, or Text Prompt) 
# and normalizes them into a "Lead Specification" for the Miner (Layer 1).

import pandas as pd
import logging
import io
import re
import difflib
from typing import List, Dict, Any, Optional, Tuple
from pydantic import BaseModel
from app.services.llm.client import llm_generate
from app.models.lead import LeadProfile

logger = logging.getLogger(__name__)

class LeadSpec(BaseModel):
    """Normalized input request for the pipeline."""
    mode: str  # "DISCOVERY" (Prompt) or "ENRICHMENT" (Excel)
    location: Optional[str] = None
    queries: List[str] = []
    provided_leads: List[LeadProfile] = []
    industry: str = "salon"  # Auto-detected: salon or solar

async def parse_user_input(
    prompt: Optional[str] = None, 
    file_content: Optional[bytes] = None, 
    file_name: Optional[str] = None
) -> LeadSpec:
    """
    Orchestrates the conversion of various input types into a LeadSpec.
    """
    if file_content:
        return await _parse_file(file_content, file_name or "unknown.csv")
    elif prompt:
        return await _parse_prompt(prompt)
    else:
        raise ValueError("No input provided (prompt or file is required).")

async def _parse_prompt(prompt: str) -> LeadSpec:
    """
    Uses an LLM to extract search parameters and industry from a natural language prompt.
    """
    logger.info(f"Parsing prompt input: {prompt}")

    system_prompt = """
    You are an AI data extractor. Extract the 'queries', 'location', and 'industry' from the user's request.
    - 'industry' MUST be either "salon" or "solar". Infer this from keywords (e.g., hair, beauty -> salon; energy, panel, solar -> solar).
    - Return ONLY valid JSON.
    - 'queries' should be a list of search terms for Google Maps.
    - 'location' should be a city/state string.
    
    Example:
    Input: "Find Indian beauty salons in Plano"
    Output: {"queries": ["Indian beauty salon", "threading parlor"], "location": "Plano, TX", "industry": "salon"}
    Input: "Show all salons in Dallas with poor websites"
    Output: {"queries": ["beauty salon", "hair salon", "cosmetology"], "location": "Dallas, TX", "industry": "salon"}
    """
    
    try:
        response = await llm_generate(prompt, system_prompt=system_prompt)
        import json
        data = json.loads(re.search(r'\{.*\}', response, re.DOTALL).group())
        
        industry = data.get("industry", "salon").lower()
        if industry not in ["salon", "solar"]:
            industry = "salon"
            
        return LeadSpec(
            mode="DISCOVERY",
            location=data.get("location"),
            queries=data.get("queries", []),
            industry=industry
        )
    except Exception as e:
        logger.warning(f"Failed to parse prompt with LLM: {str(e)}. Using fallback extraction.")
        industry = "solar" if "solar" in prompt.lower() else "salon"
        return LeadSpec(mode="DISCOVERY", location="USA", queries=[prompt], industry=industry)


def _map_columns(headers: List[str]) -> Dict[str, str]:
    """Deprecated. Using strict index-based mapping instead."""
    pass

async def _parse_file(content: bytes, filename: str) -> LeadSpec:
    """
    Parses CSV or Excel files into a list of LeadProfile skeletons for enrichment.
    Uses strict rule-based mapping (Col 1: Location/State, Col 2: Name) per user requirement.
    """
    logger.info(f"Parsing file input: {filename}")
    
    try:
        if filename.endswith(".csv"):
            df = pd.read_csv(io.BytesIO(content), header=None)
        elif filename.endswith((".xlsx", ".xls")):
            df = pd.read_excel(io.BytesIO(content), header=None)
        else:
            raise ValueError(f"Unsupported file format: {filename}")

        # Detect industry loosely from filename, default to salon
        industry = "solar" if "solar" in filename.lower() else "salon"
        country_code = "IN" if "solar" in filename.lower() else "US"
        
        leads = []
        # Aggressive phone regex that handles local and international formats
        phone_regex = re.compile(r'(?:\+?91[\-\s]?)?[6789]\d{9}|\+?1?\s*[-.\s]?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}')

        for _, row in df.iterrows():
            # Get non-null, non-empty values in order
            row_vals = [str(x).strip() for x in row.values if pd.notna(x) and str(x).strip() and str(x).lower() != 'nan']
            if not row_vals:
                 continue
            
            state = ""
            name = ""
            phone = ""
            
            # Heuristic to find state and name based on order
            filtered_vals = []
            for v in row_vals:
               # Skip purely numeric index values (handles strings like '1' and '1.0')
               try:
                   val_f = float(v)
                   # If it's a small number, it's likely an index column
                   if val_f < 10000:
                       continue
               except ValueError:
                   pass
                   
               filtered_vals.append(v)
               
            if len(filtered_vals) >= 2:
                # Based on user sample:
                # filtered_vals[0] = 'RAJASTHAN'
                # filtered_vals[1] = 'India Commercial Services'
                state = filtered_vals[0]
                name = filtered_vals[1]
                
                # Check for phone numbers in the REST of the row
                for val in filtered_vals[2:]:
                    # Heuristic for messy phones like "91 98449 33433" or "8047671280"
                    stripped_val = re.sub(r'\D', '', val)
                    if len(stripped_val) >= 10:
                         phone = val
                         break

            # Skip header rows that got caught in the parser
            header_keywords = ["phone", "email", "note", "alternate", "reachout", "index", "s.no", "state", "place"]
            if not name or any(kw in name.lower() for kw in header_keywords):
                 continue
                 
            leads.append(LeadProfile(
                name=name,
                industry=industry,
                phone=phone,
                state=state,
                country_code=country_code
            ))
            
        logger.info(f"Successfully extracted {len(leads)} {industry} leads from file via rule-based parsing.")
        
        return LeadSpec(
            mode="ENRICHMENT",
            provided_leads=leads,
            industry=industry
        )
        
    except Exception as e:
        logger.error(f"Failed to parse file: {str(e)}")
        raise

