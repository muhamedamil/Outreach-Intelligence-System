# app/services/scraper/parser.py

from bs4 import BeautifulSoup
from urllib.parse import urlparse, parse_qs


def extract_text(html: str) -> str:
    """
    Extract clean readable text from HTML.
    """

    if not html:
        return ""

    soup = BeautifulSoup(html, "html.parser")

    # Remove noise elements
    for tag in soup(["script", "style", "noscript", "iframe", "header", "footer"]):
        tag.decompose()

    text = soup.get_text(separator=" ")

    # Normalize whitespace
    cleaned = " ".join(text.split())

    # Content quality check (Lowered for search result compatibility)
    if len(cleaned) < 100:
        return ""

    return cleaned[:10000]


def extract_links(html: str) -> list:
    """
    Extract organic result URLs from search engine HTML (Google).
    """
    if not html:
        return []
        
    soup = BeautifulSoup(html, "html.parser")
    links = []
    
    # Common patterns for organic links in search pages
    for a in soup.find_all("a", href=True):
        url = a["href"]
        
        # Clean Google 'url?q=' wraps
        if "/url?q=" in url:
             try:
                 parsed = urlparse(url)
                 url = parse_qs(parsed.query).get("q", [url])[0]
             except Exception:
                 pass
             
        # Filter out junk and own domain
        if url.startswith("http") and not any(x in url for x in ["google.com", "gstatic.com", "search?", "support.google.com", "accounts.google.com", "maps.google.com"]):
            links.append(url)
            
    return list(dict.fromkeys(links))  # deduplicate