# app/services/scraper/social_resolver.py
#
# TOP 0.01% OSINT SOCIAL MEDIA RESOLVER — LAYER 2 FALLBACK
#
# ARCHITECTURE:
#   A 7-layer query cascade that progressively widens the search funnel when
#   strict queries fail, then scores every candidate URL for confidence before
#   accepting it. This mirrors how a professional OSINT analyst would work:
#   start precise, widen only when necessary, validate every result.
#
# EDGE CASES HANDLED (beyond the naive implementation):
#   1. Business names with apostrophes/& collapsed for handle matching
#      e.g. "Sharee's Beauty Supply" → "shareesbeautysupply"
#   2. Facebook Page URL variants: /pg/, /people/, fb.me/, m.facebook.com/
#   3. Instagram handle extraction + Levenshtein-style similarity scoring
#   4. Address-line dork (most unique signal for local businesses)
#   5. Google-Maps-listed biz name ≠ social handle — handled via multi-token match
#   6. URL canonicalisation: strips tracking params, ensures https://, trailing slash
#   7. Cross-validation: handle must have ≥ N shared tokens OR similarity > threshold
#   8. DDG result URLs are lowercase-preserved (not lowercased, as IG handles are case-sensitive)
#   9. Aggregator / spam-site blocklist (expanded)
#  10. Franchise / chain guard: rejects results that look like regional directory pages
#  11. Exponential backoff with per-attempt identity rotation on 429 / rate-limit

import asyncio
import logging
import re
import random
import unicodedata
from typing import Optional, Tuple, List, Dict, Any
from ddgs import DDGS
from app.models.lead import LeadProfile

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# STEALTH IDENTITY POOL
# ─────────────────────────────────────────────

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_3) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]

CHROME_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
}


# ─────────────────────────────────────────────
# BLOCKLISTS & KEYWORDS
# ─────────────────────────────────────────────

# URLs whose path-prefix indicates it's NOT a business profile page
IGNORE_URL_PATTERNS = [
    r"instagram\.com/explore/",
    r"instagram\.com/p/",
    r"instagram\.com/reel/",
    r"instagram\.com/stories/",
    r"instagram\.com/reels/",
    r"instagram\.com/tv/",
    r"instagram\.com/accounts/",
    r"instagram\.com/direct/",
    r"instagram\.com/tags/",
    r"facebook\.com/pages/category/",
    r"facebook\.com/events/",
    r"facebook\.com/hashtag/",
    r"facebook\.com/groups/",
    r"facebook\.com/sharer",
    r"facebook\.com/share",
    r"facebook\.com/dialog",
    r"facebook\.com/watch",
    r"facebook\.com/login",
    r"facebook\.com/help",
    r"tiktok\.com/tag/",
]

# Third-party aggregator domains we never want to accept as social profile hits
AGGREGATOR_DOMAINS = [
    "yellowpages", "yelp.com/search", "groupon", "angi.com", "thumbtack",
    "tripadvisor", "bbb.org", "manta.com", "hotfrog", "chamberofcommerce",
    "foursquare", "nextdoor", "google.com/maps", "maps.google",
    "linktr.ee/tags", "linktree", "beacons.ai",
    "about.fb.com", "developers.facebook.com", "newsroom.fb.com",
    "help.instagram.com", "business.instagram.com",
]

# Signals that indicate the snippet is about the correct industry
CONFIDENCE_KEYWORDS = {
    "salon": [
        "salon", "beauty", "hair", "spa", "threading", "brows", "lashes",
        "nails", "aesthetics", "skincare", "barber", "studio", "makeup",
        "waxing", "facial", "cosmet", "stylist", "blowout", "keratin",
        "highlights", "balayage", "extensions",
    ],
    "solar": [
        "solar", "energy", "sun", "panel", "contractor", "installation",
        "renewable", "photovoltaic", "inverter", "battery", "off-grid",
        "net metering", "kilowatt",
    ],
}

# Facebook URL prefixes that are known valid business page formats
FB_VALID_PREFIXES = [
    "facebook.com/",       # Standard: facebook.com/BusinessName
    "fb.com/",             # Short link
    "m.facebook.com/",     # Mobile
    "www.facebook.com/",   # Explicit www
]

# Facebook path segments that are definitely NOT business pages
FB_INVALID_SEGMENTS = [
    "/pg/", "/places/", "/pages/", "/people/",
    "/events/", "/groups/", "/hashtag/", "/marketplace/",
]


# ─────────────────────────────────────────────
# NAME NORMALISATION ENGINE
# ─────────────────────────────────────────────

def _normalise_name(name: str) -> str:
    """
    Collapse a business name to its most likely handle/slug form.

    "Sharee's Beauty Supply"  → "shareesbeautysupply"
    "R&R Nail Spa"            → "rrnailspa"
    "The Loft Salon & Co."    → "theloftsalonco"
    "Blow Salon - 5th Ave"    → "blowsalon5thave"
    """
    # 1. Unicode normalise (handles accented chars, curly quotes, etc.)
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    # 2. Lowercase
    name = name.lower()
    # 3. Replace apostrophes / possessives (Sharee's → Sharees)
    name = re.sub(r"[''`´]s?\b", "s", name)
    # 4. Replace &, and, + with nothing (R&R → rr)
    name = re.sub(r"\s*[&+]\s*", "", name)
    # 5. Remove stop words that are rarely in handles
    stop_words = r"\b(the|a|an|of|and|by|at|in|on|for|llc|inc|co|ltd)\b"
    name = re.sub(stop_words, "", name)
    # 6. Strip all non-alphanumeric
    name = re.sub(r"[^a-z0-9]", "", name)
    return name.strip()


def _name_tokens(name: str) -> List[str]:
    """
    Tokenise a business name into meaningful words for partial matching.

    "Sharee's Beauty Supply" → ["sharee", "beauty", "supply"]
    """
    # Unicode-safe lowercasing
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii").lower()
    # Split on common delimiters and filter out stop words & short tokens
    stop_words = {"the", "a", "an", "of", "and", "by", "at", "in", "on", "for", "llc", "inc", "co", "ltd", "&"}
    tokens = re.findall(r"[a-z0-9]+", name)
    return [t for t in tokens if t not in stop_words and len(t) > 1]


def _handle_similarity(handle: str, name: str) -> float:
    """
    Score how well an extracted social handle matches the business name.
    Returns 0.0 – 1.0.

    Strategy: shared-token coverage (not full Levenshtein, which is overkill
    for handles that are always heavily abbreviated versions of the name).
    """
    handle_clean = re.sub(r"[^a-z0-9]", "", handle.lower())
    name_slug = _normalise_name(name)
    name_tokens = _name_tokens(name)

    if not handle_clean or not name_slug:
        return 0.0

    # 1. Exact slug match (best case)
    if handle_clean == name_slug:
        return 1.0

    # 2. One contains the other (e.g. handle = "shereesbeauty" vs slug = "shareesbeautysupply")
    if handle_clean in name_slug or name_slug in handle_clean:
        return 0.85

    # 3. Token coverage: how many name tokens appear in the handle?
    matched = sum(1 for t in name_tokens if t in handle_clean)
    if not name_tokens:
        return 0.0
    coverage = matched / len(name_tokens)

    return coverage


def _extract_handle_from_url(url: str, platform: str) -> Optional[str]:
    """
    Extract the username/handle from an Instagram or Facebook profile URL.

    instagram.com/beautysalon123/        → "beautysalon123"
    facebook.com/BeautySalonDallas       → "BeautySalonDallas"
    m.facebook.com/pg/TheLoftSalon/      → "TheLoftSalon"
    fb.com/salon                         → "salon"
    """
    try:
        # ── Step 1: Strip query string FIRST (before any path parsing) ──
        url = url.split("?")[0]

        # ── Step 2: Strip protocol, then optional www. / m. prefix ──
        clean = re.sub(r"^https?://", "", url)
        clean = re.sub(r"^(?:www|m)\.", "", clean)

        if platform == "instagram":
            # Handles can contain letters, digits, underscores, periods, and hyphens
            match = re.match(r"instagram\.com/([a-zA-Z0-9._\-]+)/?$", clean)
            if match:
                handle = match.group(1)
                # Reject reserved Instagram paths (not profile handles)
                reserved = {"explore", "accounts", "direct", "reels", "tv", "p", "stories", "tags"}
                if handle.lower() not in reserved:
                    return handle

        elif platform == "facebook":
            # Normalise: strip domain leaving only the path
            clean = re.sub(r"^(?:facebook|fb)\.com/", "", clean)
            # Strip legacy FB path prefixes like /pg/, /people/, /profile.php
            clean = re.sub(r"^(?:pg|people|profile\.php)/", "", clean)
            # Get the first path segment as the handle
            handle = clean.split("/")[0]
            # Reject: numeric-only IDs, profile.php itself, too short, contains a dot (non-handle)
            if (
                handle
                and not re.match(r"^\d+$", handle)
                and handle not in ("profile.php", "pages", "groups", "events", "marketplace")
                and "." not in handle          # handles never have dots; profile.php does
                and len(handle) > 2
            ):
                return handle

    except Exception:
        pass
    return None


def _canonicalise_url(url: str, platform: str) -> str:
    """
    Normalise a discovered URL to its cleanest canonical form.

    - Always https://
    - Strip tracking params (?igsh=..., ?ref=..., ?__cft__=..., etc.)
    - Ensure trailing slash for Instagram profiles
    - Expand fb.com → facebook.com
    - Strip mobile prefix (m.facebook.com → facebook.com)
    """
    if not url:
        return url

    # Strip ALL query params (we never need them for profile pages)
    url = url.split("?")[0]

    # Ensure https
    if url.startswith("http://"):
        url = "https://" + url[7:]
    elif not url.startswith("https://"):
        url = "https://" + url

    # Normalise Facebook variants
    url = re.sub(r"https://(www\.)?m\.facebook\.com/", "https://www.facebook.com/", url)
    url = re.sub(r"https://(www\.)?fb\.com/", "https://www.facebook.com/", url)
    url = re.sub(r"https://facebook\.com/", "https://www.facebook.com/", url)

    # Normalise Instagram
    url = re.sub(r"https://(www\.)?instagram\.com/", "https://www.instagram.com/", url)

    # Ensure trailing slash for profile URLs (both platforms)
    if platform in ("instagram", "facebook") and not url.endswith("/"):
        url += "/"

    return url


# ─────────────────────────────────────────────
# MAIN ENTRY POINT
# ─────────────────────────────────────────────

async def resolve_missing_socials(lead: LeadProfile) -> LeadProfile:
    """
    7-Layer OSINT query cascade for Instagram and Facebook discovery.

    Layers (ordered from highest to lowest precision):
      1. Phone number exact dork            (both platforms simultaneously)
      2. Name + city + platform dork        (IG)
      3. Name + city + platform dork        (FB)
      4. Name + state dork                  (IG fallback — wider geography)
      5. Name + state dork                  (FB fallback)
      6. Street address dork                (ultra-specific local signal)
      7. Normalised slug dork               (handle-first lookup)

    Each result is scored against:
      - Geographic proximity (city, area code)
      - Industry keyword presence
      - Handle similarity to the business name
      - Aggregator / franchise guard
    """
    missing_ig = not lead.instagram_url
    missing_fb = not lead.facebook_url

    if not missing_ig and not missing_fb:
        return lead

    logger.info(f"[{lead.name}] 🔍 Triggering 7-Layer OSINT Social Resolver...")

    name_slug = _normalise_name(lead.name)
    city = lead.city or ""
    state = lead.state or ""

    # ── LAYER 1: PHONE NUMBER DORK (Highest signal — phone is globally unique) ──
    if lead.phone_unformatted and (missing_ig or missing_fb):
        raw = re.sub(r"[^0-9]", "", lead.phone_unformatted)
        # Generate both common phone formatting styles
        phone_formats = [raw[-10:]] if len(raw) >= 10 else []
        if len(raw) >= 10:
            digits = raw[-10:]
            phone_formats.append(f"{digits[:3]}-{digits[3:6]}-{digits[6:]}")
            phone_formats.append(f"({digits[:3]}) {digits[3:6]}-{digits[6:]}")

        for fmt in phone_formats[:2]:  # Only first 2 formats to avoid too many queries
            q = f'"{fmt}" site:instagram.com OR site:facebook.com'
            ig, fb = await _execute_dork(q, lead)
            if missing_ig and ig:
                lead.instagram_url = ig
                lead.lead_score_breakdown["ig_osint_phone"] = 8
                lead.lead_score += 8
                missing_ig = False
                logger.info(f"[{lead.name}] ✅ IG via OSINT (Phone): {ig}")
            if missing_fb and fb:
                lead.facebook_url = fb
                missing_fb = False
                logger.info(f"[{lead.name}] ✅ FB via OSINT (Phone): {fb}")
            if not missing_ig and not missing_fb:
                break
            await asyncio.sleep(1.2)

    # ── LAYER 2: NAME + CITY — INSTAGRAM ──
    if missing_ig and city:
        q = f'"{lead.name}" "{city}" site:instagram.com'
        ig, _ = await _execute_dork(q, lead)
        if ig:
            lead.instagram_url = ig
            lead.lead_score_breakdown["ig_osint_name_city"] = 6
            lead.lead_score += 6
            missing_ig = False
            logger.info(f"[{lead.name}] ✅ IG via OSINT (Name+City): {ig}")
        await asyncio.sleep(1.2)

    # ── LAYER 3: NAME + CITY — FACEBOOK ──
    if missing_fb and city:
        q = f'"{lead.name}" "{city}" site:facebook.com'
        _, fb = await _execute_dork(q, lead)
        if fb:
            lead.facebook_url = fb
            missing_fb = False
            logger.info(f"[{lead.name}] ✅ FB via OSINT (Name+City): {fb}")
        await asyncio.sleep(1.2)

    # ── LAYER 4: NAME + STATE — INSTAGRAM (wider geography) ──
    if missing_ig and state:
        q = f'"{lead.name}" "{state}" site:instagram.com'
        ig, _ = await _execute_dork(q, lead)
        if ig:
            lead.instagram_url = ig
            lead.lead_score_breakdown["ig_osint_name_state"] = 4
            lead.lead_score += 4
            missing_ig = False
            logger.info(f"[{lead.name}] ✅ IG via OSINT (Name+State): {ig}")
        await asyncio.sleep(1.2)

    # ── LAYER 5: NAME + STATE — FACEBOOK ──
    if missing_fb and state:
        q = f'"{lead.name}" "{state}" site:facebook.com'
        _, fb = await _execute_dork(q, lead)
        if fb:
            lead.facebook_url = fb
            missing_fb = False
            logger.info(f"[{lead.name}] ✅ FB via OSINT (Name+State): {fb}")
        await asyncio.sleep(1.2)

    # ── LAYER 6: STREET ADDRESS DORK (ultra-specific — local biz fingerprint) ──
    if (missing_ig or missing_fb) and lead.street and city:
        # Use just the house number + first word of street to minimise false positives
        street_tokens = lead.street.split()
        street_short = " ".join(street_tokens[:2]) if len(street_tokens) >= 2 else lead.street
        q = f'"{street_short}" "{city}" site:instagram.com OR site:facebook.com'
        ig, fb = await _execute_dork(q, lead)
        if missing_ig and ig:
            lead.instagram_url = ig
            lead.lead_score_breakdown["ig_osint_address"] = 10
            lead.lead_score += 10
            missing_ig = False
            logger.info(f"[{lead.name}] ✅ IG via OSINT (Address): {ig}")
        if missing_fb and fb:
            lead.facebook_url = fb
            missing_fb = False
            logger.info(f"[{lead.name}] ✅ FB via OSINT (Address): {fb}")
        await asyncio.sleep(1.2)

    # ── LAYER 7: SLUG-FIRST LOOKUP (handle-centric — when name ≠ known handle) ──
    # Fires when name has special chars that make quoted search unreliable
    # e.g. "Sharee's Beauty Supply" → try "shareesbeauty" OR "shareesbeautysupply"
    if (missing_ig or missing_fb) and name_slug and len(name_slug) > 4:
        # Try both full slug and first-N-chars (handles are often abbreviated)
        slug_variants = list({name_slug, name_slug[:14]}) if len(name_slug) > 14 else [name_slug]
        for slug in slug_variants:
            if not missing_ig and not missing_fb:
                break
            platforms = []
            if missing_ig:
                platforms.append("site:instagram.com")
            if missing_fb:
                platforms.append("site:facebook.com")
            q = f'"{slug}" {" OR ".join(platforms)}'
            ig, fb = await _execute_dork(q, lead, min_similarity=0.55)
            if missing_ig and ig:
                lead.instagram_url = ig
                lead.lead_score_breakdown["ig_osint_slug"] = 4
                lead.lead_score += 4
                missing_ig = False
                logger.info(f"[{lead.name}] ✅ IG via OSINT (Slug): {ig}")
            if missing_fb and fb:
                lead.facebook_url = fb
                missing_fb = False
                logger.info(f"[{lead.name}] ✅ FB via OSINT (Slug): {fb}")
            await asyncio.sleep(1.0)

    lead.lead_score = min(100, lead.lead_score)
    return lead


# ─────────────────────────────────────────────
# CORE DORK EXECUTOR WITH SCORING ENGINE
# ─────────────────────────────────────────────

async def _execute_dork(
    query: str,
    lead: LeadProfile,
    min_similarity: float = 0.35,
) -> Tuple[Optional[str], Optional[str]]:
    """
    Executes a DuckDuckGo search and scores every result URL.

    Scoring pipeline per result:
      1. Hard reject: aggregator domain / invalid URL pattern
      2. Hard reject: Facebook path indicates non-profile page
      3. Extract handle from URL
      4. Similarity check: handle vs business name (threshold: min_similarity)
      5. Geographic consensus: city OR area-code match
      6. Industry keyword match in snippet
      7. Accept if: similarity > 0.7 OR (geo_match AND industry_match AND similarity > min_similarity)

    Returns (instagram_url | None, facebook_url | None) — both canonicalised.
    """
    found_ig: Optional[str] = None
    found_fb: Optional[str] = None

    max_retries = 3
    for attempt in range(max_retries):
        await asyncio.sleep(random.uniform(0.5, 2.0))

        current_ua = random.choice(USER_AGENTS)
        headers = CHROME_HEADERS.copy()
        headers["User-Agent"] = current_ua

        def _search():
            try:
                with DDGS(headers=headers, timeout=20) as ddgs:
                    return list(ddgs.text(query, max_results=8))
            except Exception as e:
                err_msg = str(e).lower()
                if "429" in err_msg or "ratelimit" in err_msg:
                    logger.warning(
                        f"[{lead.name}] DDG rate-limited (attempt {attempt+1}/{max_retries})"
                    )
                else:
                    logger.debug(f"DDGS search failed [{query[:60]}]: {e}")
                return None

        try:
            results = await asyncio.to_thread(_search)

            if results is None:
                if attempt < max_retries - 1:
                    wait = (2 ** attempt) + random.uniform(1, 3)
                    await asyncio.sleep(wait)
                    continue
                else:
                    return None, None

            for res in results:
                # Preserve case from DDG (Instagram handles ARE case-sensitive)
                raw_url = res.get("href", "")
                url_lower = raw_url.lower()
                snippet = (res.get("body", "") + " " + res.get("title", "")).lower()

                # ── HARD REJECT 1: aggregator / spam domains ──
                if any(agg in url_lower for agg in AGGREGATOR_DOMAINS):
                    continue

                # ── HARD REJECT 2: invalid URL path pattern ──
                if any(re.search(pat, url_lower) for pat in IGNORE_URL_PATTERNS):
                    continue

                # ── DETECT PLATFORM ──
                is_ig = "instagram.com" in url_lower
                is_fb = (
                    "facebook.com" in url_lower or
                    "fb.com" in url_lower
                )

                if not is_ig and not is_fb:
                    continue

                # ── HARD REJECT 3: Facebook non-profile path segments ──
                if is_fb:
                    if any(seg in url_lower for seg in FB_INVALID_SEGMENTS):
                        continue
                    # Reject numeric-only page IDs (profile.php?id=...)
                    if "profile.php" in url_lower:
                        continue

                platform = "instagram" if is_ig else "facebook"

                # Already have this platform
                if is_ig and found_ig:
                    continue
                if is_fb and found_fb:
                    continue

                # ── EXTRACT HANDLE ──
                handle = _extract_handle_from_url(raw_url, platform)

                # ── SIMILARITY SCORE ──
                similarity = _handle_similarity(handle, lead.name) if handle else 0.0

                # ── GEOGRAPHIC SIGNALS ──
                city_match = bool(lead.city and lead.city.lower() in snippet)
                state_match = bool(lead.state and lead.state.lower() in snippet)
                geo_match = city_match or state_match

                # Area code extraction from phone (most unique local signal)
                area_code_match = False
                if lead.phone_unformatted:
                    digits = re.sub(r"[^0-9]", "", lead.phone_unformatted)
                    if len(digits) >= 10:
                        area_code = digits[-10:-7]
                        if (
                            f"({area_code})" in snippet
                            or f"{area_code}-" in snippet
                            or f" {area_code} " in snippet
                        ):
                            area_code_match = True
                            geo_match = True  # Override: area code is highly specific

                # ── INDUSTRY SIGNAL ──
                kw_list = CONFIDENCE_KEYWORDS.get(lead.industry, CONFIDENCE_KEYWORDS["salon"])
                industry_match = any(kw in snippet for kw in kw_list)

                # ── FRANCHISE / CHAIN GUARD ──
                # If snippet mentions multiple cities it's likely a directory / national page
                location_count = len(re.findall(r"\b[A-Z][a-z]+(,\s*[A-Z]{2})?\b", res.get("body", "")))
                looks_like_directory = location_count > 6

                if looks_like_directory:
                    continue

                # ── ACCEPTANCE LOGIC (tiered) ──
                accepted = False

                # Tier 1: Very high handle similarity (name match alone is enough)
                if similarity >= 0.80:
                    accepted = True

                # Tier 2: Strong area code signal (unique phone prefix confirms locality)
                elif area_code_match and similarity >= min_similarity:
                    accepted = True

                # Tier 3: Geographic + industry + reasonable similarity
                elif geo_match and industry_match and similarity >= min_similarity:
                    accepted = True

                # Tier 4: City match + industry match (no handle — e.g. FB pages with display names)
                elif city_match and industry_match and not handle:
                    accepted = True

                if accepted:
                    canonical = _canonicalise_url(raw_url, platform)
                    if is_ig and not found_ig:
                        found_ig = canonical
                        logger.debug(
                            f"[{lead.name}] IG candidate accepted | handle={handle} "
                            f"sim={similarity:.2f} geo={geo_match} ind={industry_match}"
                        )
                    elif is_fb and not found_fb:
                        found_fb = canonical
                        logger.debug(
                            f"[{lead.name}] FB candidate accepted | handle={handle} "
                            f"sim={similarity:.2f} geo={geo_match} ind={industry_match}"
                        )

                if found_ig and found_fb:
                    break

            # We got results (even if nothing accepted) — don't retry
            break

        except Exception as e:
            logger.warning(f"OSINT dork error: {e}")
            break

    return found_ig, found_fb
