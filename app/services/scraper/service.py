# app/services/scraper/service.py

import asyncio
from typing import List, Dict

from app.services.scraper.fetcher import fetch_html
from app.services.scraper.parser import extract_text


# Limit concurrency to avoid blocking / rate limiting
SEMAPHORE = asyncio.Semaphore(6)


def is_valid_url(url: str) -> bool:
    """
    Basic URL filtering to avoid junk links.
    """
    if not url:
        return False

    blocked_keywords = ["duckduckgo", "youtube", "facebook", "instagram", ".pdf"]

    if not url.startswith("http"):
        return False

    if any(keyword in url.lower() for keyword in blocked_keywords):
        return False

    return True


async def scrape_url(url: str) -> Dict:
    """
    Scrape a single URL with concurrency control.
    """

    if not is_valid_url(url):
        return {"url": url, "content": "", "success": False}

    async with SEMAPHORE:
        html = await fetch_html(url)

        if not html:
            return {"url": url, "content": "", "success": False}

        text = extract_text(html)

        if not text:
            return {"url": url, "content": "", "success": False}

        return {"url": url, "content": text, "success": True}


async def scrape_multiple(urls: List[str]) -> List[Dict]:
    """
    Scrape multiple URLs in parallel with filtering.
    """

    if not urls:
        return []

    # Deduplicate URLs
    unique_urls = list(set(urls))

    tasks = [scrape_url(url) for url in unique_urls]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    cleaned_results = []

    for result in results:
        if isinstance(result, dict) and result.get("success"):
            cleaned_results.append(result)

    return cleaned_results
