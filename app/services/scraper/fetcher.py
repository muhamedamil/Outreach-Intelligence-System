# app/services/scraper/fetcher.py

import httpx
import asyncio

HEADERS = {
    "User-Agent": "Mozilla/5.0"
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
                timeout=10
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