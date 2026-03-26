# app/services/scraper/fetcher.py

import httpx
import asyncio
from app.config.settings import settings

timeout = settings.SCRAPER_TIMEOUT

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9"
}


class HTTPClient:
    """
    Singleton-style async HTTP client for connection reuse.
    """

    _client: httpx.AsyncClient = None

    @classmethod
    def get_client(cls) -> httpx.AsyncClient:
        if cls._client is None:
            cls._client = httpx.AsyncClient(
                headers=HEADERS,
                follow_redirects=True,
                timeout= timeout
            )
        return cls._client


async def fetch_html(
    url: str,
    retries: int = 2,
    backoff_factor: float = 1.5
) -> str:
    """
    Fetch HTML with retry + exponential backoff.
    """

    client = HTTPClient.get_client()

    for attempt in range(retries + 1):
        try:
            response = await client.get(url)
            response.raise_for_status()

            if "text/html" not in response.headers.get("content-type", ""):
                return ""

            return response.text

        except Exception:
            if attempt == retries:
                return ""

            await asyncio.sleep(backoff_factor ** attempt)

    return ""