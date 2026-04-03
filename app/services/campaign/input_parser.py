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
    """Smart fuzzy mapper using synonym checking and difflib for messy Excel files."""
    mapping = {
        "name": ["name", "company", "business", "organization", "salon", "firm", "biz", "org", "account", "provider"],
        "phone": ["phone", "ph", "mobile", "contact", "tel", "cell"],
        "city": ["city", "town", "location"],
        "state": ["state", "st", "province"],
        "address": ["address", "street", "addr"]
    }
    
    result = {}
    for target, synonyms in mapping.items():
        found = False
        # 1. Broad substring match (highest priority for common terms)
        for h in headers:
            h_lower = str(h).lower().strip()
            if any(syn in h_lower for syn in synonyms):
                result[target] = h
                found = True
                break
        
        # 2. Fuzzy match fallback
        if not found:
            for syn in synonyms:
                matches = difflib.get_close_matches(syn, [str(h) for h in headers], n=1, cutoff=0.7)
                if matches:
                    result[target] = matches[0]
                    break
    return result

async def _parse_file(content: bytes, filename: str) -> LeadSpec:
    """
    Parses CSV or Excel files into a list of LeadProfile skeletons for enrichment,
    using robust fuzzy column matching.
    """
    logger.info(f"Parsing file input: {filename}")
    
    try:
        if filename.endswith(".csv"):
            df = pd.read_csv(io.BytesIO(content))
        elif filename.endswith((".xlsx", ".xls")):
            df = pd.read_excel(io.BytesIO(content))
        else:
            raise ValueError(f"Unsupported file format: {filename}")

        columns = df.columns.tolist()
        col_map = _map_columns(columns)
        logger.info(f"Fuzzy Column Mapping Result: {col_map}")
        
        # Detect industry loosely from filename, default to salon
        industry = "solar" if "solar" in filename.lower() else "salon"
        
        leads = []
        for _, row in df.iterrows():
            name = None
            if "name" in col_map:
                name = row.get(col_map["name"])
            else:
                name = row.iloc[0]  # Ultimate fallback: assume first column is the name
                
            if not name or pd.isna(name):
                continue
                
            city = row.get(col_map.get("city", ""), "") if "city" in col_map else ""
            state = row.get(col_map.get("state", ""), "") if "state" in col_map else ""
            
            # Combine city & state intelligently (Layer 0 handling location spread across columns)
            location_str = str(city) if pd.notna(city) else ""
            if pd.notna(state) and str(state).strip():
                location_str += f", {str(state)}"
                
            phone = str(row.get(col_map["phone"], "")) if "phone" in col_map and pd.notna(row.get(col_map["phone"])) else ""
            address = str(row.get(col_map["address"], "")) if "address" in col_map and pd.notna(row.get(col_map["address"])) else ""
            
            leads.append(LeadProfile(
                name=str(name),
                industry=industry,
                phone=phone,
                city=location_str.strip(", "),
                address=address
            ))
            
        logger.info(f"Successfully extracted {len(leads)} {industry} leads from file.")
        
        return LeadSpec(
            mode="ENRICHMENT",
            provided_leads=leads,
            industry=industry
        )
        
    except Exception as e:
        logger.error(f"Failed to parse file: {str(e)}")
        raise

