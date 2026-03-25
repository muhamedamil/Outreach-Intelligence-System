# app/agents/researcher/tools.py

import httpx
from bs4 import BeautifulSoup
from typing import List
from app.config.settings import settings

from app.config.settings import settings


timeout = settings.SCRAPER_TIMEOUT


HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

SEARCH_URL = "https://duckduckgo.com/html/"


async def search_duckduckgo(query: str, max_results: int = 5) -> List[str]:
    async with httpx.AsyncClient(headers=HEADERS) as client:
        try:
            response = await client.post(
                SEARCH_URL,
                data={"q": query},
                timeout=timeout
            )
        except Exception:
            return []

    soup = BeautifulSoup(response.text, "html.parser")

    links = []
    for a in soup.select(".result__a"):
        href = a.get("href")
        if href and href.startswith("http"):
            links.append(href)

    return links[:max_results]