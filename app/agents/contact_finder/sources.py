# app/agents/contact_finder/sources.py

from typing import List, Optional
from urllib.parse import urlparse, urljoin, quote_plus


# -------------------------
# NORMALIZATION
# -------------------------
def normalize_website(url: Optional[str]) -> Optional[str]:
    """
    Normalize website URL:
    - Adds scheme if missing
    - Validates structure
    - Removes trailing slash
    """

    if not url:
        return None

    # Cast to string in case it's a pydantic.Url object
    url = str(url).strip()

    # Add scheme if missing
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        parsed = urlparse(url)
    except Exception:
        return None

    # Basic validation
    if not parsed.netloc:
        return None

    return url.rstrip("/")


# -------------------------
# COMMON CONTACT PATHS
# -------------------------
COMMON_PATHS = [
    "",
    "/contact",
    "/contact-us",
    "/contactus",
    "/about",
    "/about-us",
    "/support",
    "/help",
    "/reach-us",
    "/get-in-touch",
    "/company",
    "/info"
]


def build_website_urls(base_url: str) -> List[str]:
    """
    Generate high-probability contact/info pages.
    """

    urls = []

    for path in COMMON_PATHS:
        try:
            full_url = urljoin(base_url + "/", path.lstrip("/"))
            urls.append(full_url.rstrip("/"))
        except Exception:
            continue

    return urls


# -------------------------
# DIRECTORY FALLBACKS
# -------------------------
def build_directory_urls(
    company: Optional[str],
    location: Optional[str]
) -> List[str]:
    """
    Build directory/search URLs.

    These are fallback signals and should NOT always be scraped blindly.
    """

    if not company:
        return []

    query = f"{company} {location or ''}".strip()
    encoded_query = quote_plus(query)

    return [
        # Google search
        f"https://www.google.com/search?q={encoded_query}",

        # Justdial
        f"https://www.justdial.com/search?q={encoded_query}",

        # IndiaMART
        f"https://www.indiamart.com/search.mp?ss={encoded_query}",

        # Yelp (optional)
        f"https://www.yelp.com/search?find_desc={encoded_query}",
    ]


# -------------------------
# URL VALIDATION / FILTERING
# -------------------------
def is_valid_contact_url(url: str) -> bool:
    """
    Basic filtering to avoid useless scraping.
    """

    if not url:
        return False

    url = url.lower()

    blocked_patterns = [
        ".pdf",
        ".jpg",
        ".jpeg",
        ".png",
        "facebook.com",
        "instagram.com",
        "twitter.com",
        "linkedin.com/jobs",
        "youtube.com"
    ]

    if any(pattern in url for pattern in blocked_patterns):
        return False

    if not url.startswith("http"):
        return False

    return True


# -------------------------
# DEDUPLICATION
# -------------------------
def deduplicate_urls(urls: List[str]) -> List[str]:
    """
    Remove duplicates while preserving order.
    """

    seen = set()
    unique_urls = []

    for url in urls:
        if url not in seen:
            seen.add(url)
            unique_urls.append(url)

    return unique_urls


# -------------------------
# MAIN ENTRY POINT
# -------------------------
def build_contact_sources(
    website: Optional[str],
    company_name: Optional[str] = None,
    location: Optional[str] = None,
    include_directories: bool = False
) -> List[str]:
    """
    Build contact discovery URLs.

    Strategy:
    1. Normalize website
    2. Generate website contact pages (high priority)
    3. Optionally include directory search URLs (low priority)
    4. Filter invalid URLs
    5. Deduplicate
    """

    urls: List[str] = []

    # Step 1: Website-based sources
    normalized = normalize_website(website)

    if normalized:
        urls.extend(build_website_urls(normalized))

    # Step 2: Optional directory sources (controlled)
    if include_directories and company_name:
        urls.extend(build_directory_urls(company_name, location))

    # Step 3: Filter invalid URLs
    urls = [url for url in urls if is_valid_contact_url(url)]

    # Step 4: Deduplicate
    urls = deduplicate_urls(urls)

    return urls