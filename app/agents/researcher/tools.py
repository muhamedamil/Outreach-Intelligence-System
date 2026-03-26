# app/agents/researcher/tools.py

import logging
from typing import List
from tavily import TavilyClient
from app.config.settings import settings

logger = logging.getLogger(__name__)

# Initialize Tavily Client
try:
    tavily = TavilyClient(api_key=settings.TAVILY_API_KEY)
except Exception as e:
    logger.error(f"Failed to initialize Tavily client: {e}")
    tavily = None

async def search_tavily(
    query: str, 
    max_results: int = 5, 
    include_raw: bool = False, 
    search_depth: str = "advanced",
    include_answer: bool = True
) -> List[dict]:
    """
    Search using Tavily API and return a list of result dictionaries.
    """
    if not tavily:
        logger.error("Tavily client not initialized. Cannot perform search.")
        return []

    logger.info(f"search_tavily starting query: {query} (depth: {search_depth})")
    
    try:
        response = tavily.search(
            query=query, 
            search_depth=search_depth, 
            max_results=max_results,
            include_raw_content=include_raw,
            include_answer=include_answer
        )
        
        results = response.get("results", [])
        answer = response.get("answer")
        
        # Inject the synthesized answer into the results for easier extraction
        if answer and results:
            results[0]["answer"] = answer
            
        logger.info(f"Tavily returned {len(results)} results.")
        return results
        
    except Exception as e:
        logger.error(f"Tavily search error ({search_depth}): {str(e)}")
        
        # Fallback to basic if advanced failed (common for 432/subscription errors)
        if search_depth == "advanced":
            logger.info("Retrying with basic search depth...")
            try:
                response = tavily.search(
                    query=query, 
                    search_depth="basic", 
                    max_results=max_results,
                    include_raw_content=include_raw,
                    include_answer=include_answer
                )
                return response.get("results", [])
            except Exception as basic_e:
                logger.error(f"Tavily basic search also failed: {str(basic_e)}")
        
        return []

# Deprecated: keeping for reference during migration if needed
async def search_duckduckgo(query: str, max_results: int = 5) -> List[str]:
    logger.warning("search_duckduckgo is deprecated. Use search_tavily instead.")
    return []