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
        raw_url = a["href"]
        url = raw_url
        
        # Clean Google 'url?q=' wraps
        if "/url?q=" in url:
             try:
                  parsed = urlparse(url)
                  url = parse_qs(parsed.query).get("q", [url])[0]
             except Exception:
                  pass
              
        # Filter out junk and internal engine links
        is_internal = any(x in url.lower() for x in ["google.com/search", "google.com/maps", "googleadservices", "webcache.googleusercontent", "google.com/shopping"])
        
        if url.startswith("http") and not is_internal:
            # Avoid obvious navigational links
            if any(x in url.lower() for x in ["support.google", "accounts.google", "policies.google", "github.com/google"]):
                 continue
            
            links.append(url)
            
    return list(dict.fromkeys(links))  # deduplicate