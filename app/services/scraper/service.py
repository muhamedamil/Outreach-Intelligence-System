# app/services/scraper/service.py

import asyncio
from typing import List, Dict

from app.services.scraper.fetcher import fetch_html
from app.services.scraper.parser import extract_text
import logging

logger = logging.getLogger(__name__)


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
        logger.warning(f"Skipping scrape for invalid or blocked URL: {url}")
        return {"url": url, "content": "", "success": False}

    logger.info(f"Starting scrape for URL: {url}")
    async with SEMAPHORE:
        html = await fetch_html(url)

        if not html:
            logger.warning(f"Scrape resulted in empty HTML for: {url}")
            return {"url": url, "content": "", "success": False}

        text = extract_text(html)

        if not text:
            logger.warning(f"Extraction yielded empty text for: {url}")
            return {"url": url, "content": "", "success": False}

        logger.info(f"Successfully extracted {len(text)} chars from {url}")
        return {"url": url, "content": text, "success": True}


async def scrape_multiple(urls: List[str]) -> List[Dict]:
    """
    Scrape multiple URLs in parallel with filtering.
    """

    if not urls:
        logger.warning("scrape_multiple called with empty URLs list.")
        return []

    # Deduplicate URLs
    unique_urls = list(set(urls))

    tasks = [scrape_url(url) for url in unique_urls]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    cleaned_results = []

    for result in results:
        if isinstance(result, dict) and result.get("success"):
            cleaned_results.append(result)
        elif isinstance(result, Exception):
            logger.error(f"scrape_multiple task exception: {result}")

    logger.info(f"scrape_multiple finished with {len(cleaned_results)} successful scrapes out of {len(tasks)} tasks.")
    return cleaned_results
