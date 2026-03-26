# app/agents/researcher/tools.py

import logging
from typing import List
from tavily import TavilyClient
from app.config.settings import settings

logger = logging.getLogger(__name__)

import httpx

# Initialize settings-based key
TAVILY_API_URL = "https://api.tavily.com/search"

async def search_tavily(
    query: str, 
    max_results: int = 5, 
    include_raw: bool = False, 
    search_depth: str = "advanced",
    include_answer: bool = True
) -> List[dict]:
    """
    Search using Tavily API via direct httpx call.
    """
    api_key = settings.TAVILY_API_KEY.strip() if settings.TAVILY_API_KEY else None
    
    if not api_key:
        logger.error("TAVILY_API_KEY is missing. Cannot perform search.")
        return []

    logger.info(f"search_tavily starting query: {query} (depth: {search_depth})")
    
    payload = {
        "api_key": api_key,
        "query": query,
        "search_depth": search_depth,
        "max_results": max_results,
        "include_raw_content": include_raw,
        "include_answer": include_answer
    }

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Content-Type": "application/json"
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(TAVILY_API_URL, json=payload, headers=headers)
            
            if response.status_code != 200:
                logger.error(f"Tavily API error: {response.status_code} | {response.text}")
                
                # Fallback to basic if advanced failed (common for 432/subscription errors)
                if search_depth == "advanced" and response.status_code != 401:
                    logger.info("Retrying with basic search depth...")
                    payload["search_depth"] = "basic"
                    basic_resp = await client.post(TAVILY_API_URL, json=payload)
                    if basic_resp.status_code == 200:
                        response = basic_resp
                    else:
                        return []
                else:
                    return []

            data = response.json()
            results = data.get("results", [])
            answer = data.get("answer")
            
            if answer and results:
                results[0]["answer"] = answer
                
            logger.info(f"Tavily returned {len(results)} results.")
            return results
            
    except Exception as e:
        logger.error(f"Tavily request failed: {str(e)}")
        return []

# Deprecated: keeping for reference during migration if needed
async def search_duckduckgo(query: str, max_results: int = 5) -> List[str]:
    logger.warning("search_duckduckgo is deprecated. Use search_tavily instead.")
    return []