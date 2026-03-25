# app/services/scraper/parser.py

from bs4 import BeautifulSoup


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

    # Content quality check
    if len(cleaned) < 200:
        return ""

    return cleaned[:10000]