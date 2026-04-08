# app/services/gmaps/apify_client.py
#
# "Top 1%" Google Maps Discovery Engine
# Uses compass/crawler-google-places to extract structured salon data.
#
# PRODUCTION HARDENING:
#   1. Deduplication by place_id
#   2. Category filtering (only beauty-related businesses)
#   3. Phone number normalization to +1XXXXXXXXXX
#   4. Graceful partial results on failure
#   5. Proper error propagation with context

import re
import logging
from typing import List, Optional, Dict, Any, Set
from apify_client import ApifyClient
from app.config.settings import settings
from app.models.lead import (
    LeadProfile, LeadCategory,
    ReviewDistribution, Review, OpeningHoursEntry
)

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# APIFY ACTOR CONFIG
# ─────────────────────────────────────────────
ACTOR_ID = "compass/crawler-google-places"

# Booking/Quoting platform domains that Apify might return
KNOWN_AUTOMATION_DOMAINS = [
    # Salon
    "vagaro.com", "mindbodyonline.com", "mndbdy.ly", "fresha.com",
    "calendly.com", "acuityscheduling.com", "phorest.com",
    "glossgenius.com", "joinblvd.com", "booksy.com", "styleseat.com",
    "squareup.com/appointments", "setmore.com", "simplybook.me",
    "square.site", "boulevard.io",
    # Solar
    "aurorasolar.com", "solargraf.com", "opensolar.com", "energysage.com",
    "sunrun.com/get-a-quote", "sunnova.com", "powur.com"
]

# ── PRODUCTION FIX #2: Category whitelist (Dynamic) ──
INDUSTRY_CATEGORY_KEYWORDS = {
    "salon": [
        "salon", "beauty", "hair", "spa", "threading", "waxing",
        "nail", "barber", "cosmet", "skincare", "facial", "lash",
        "brow", "makeup", "mehndi", "henna", "bridal", "ayurvedic",
        "indian", "ethnic", "parlor", "parlour", "stylist", 
        "aesthet", "massage", "wellness",
    ],
    "solar": [
        "solar", "energy", "sun panel", "solar contractor", 
        "solar equipment", "renewable", "solar installation",
        "green energy", "solar power"
    ]
}

# ── PRODUCTION FIX #4: Phone normalization ──
US_PHONE_CLEAN_REGEX = re.compile(r'[^\d+]')


# ─────────────────────────────────────────────
# CORE DISCOVERY FUNCTION
# ─────────────────────────────────────────────

async def search_leads(
    location: str,
    queries: List[str],
    industry: str = "salon",
    max_results: Optional[int] = None,
    include_reviews: bool = True,
    max_reviews: int = 5,
) -> List[LeadProfile]:
    """
    Discovers leads via Google Maps using Apify.
    
    Production features:
    - Deduplicates results by place_id
    - Filters to beauty-related categories only
    - Normalizes all phone numbers to +1XXXXXXXXXX
    - Returns partial results on failure instead of empty list
    """
    
    max_results = max_results or settings.APIFY_MAX_RESULTS
    
    # ── PRODUCTION DYNAMIC BUFFERING ──
    # Survival rate through strict filters (closed, reviews 10-1000, category) is ~25%.
    # Multiplier ensures we scrape enough raw leads to fulfill the exact user request.
    yield_multiplier = 4.0
    total_needed = int(max_results * yield_multiplier)
    
    # Distribute the total needed across the number of queries to avoid runaway costs
    num_queries = max(1, len(queries))
    # Cap maximum at 100 per query to prevent extreme scenarios, floor at 5.
    crawl_limit = max(5, min(100, int(total_needed / num_queries)))
    
    logger.info(f"🔍 Starting Apify Discovery")
    logger.info(f"   Location: {location}")
    logger.info(f"   Queries:  {queries}")
    logger.info(f"   Target Leads: {max_results} | Crawl Limit p/q: {crawl_limit}")
    logger.info(f"   Review threshold: {settings.REVIEW_MIN}-{settings.REVIEW_MAX}")

    client = ApifyClient(settings.APIFY_TOKEN)

    run_input = {
        "searchStringsArray": queries,
        "locationQuery": location,
        "maxCrawledPlacesPerSearch": crawl_limit,
        "language": "en",
        "proxyConfiguration": {"useApifyProxy": True},
        "skipClosedPlaces": True,
        "maxReviews": max_reviews if include_reviews else 0,
        "reviewsSort": "newest",
    }

    try:
        logger.info(f"   Calling Apify actor: {ACTOR_ID}...")
        run = client.actor(ACTOR_ID).call(run_input=run_input)
        
        if not run or "defaultDatasetId" not in run:
            logger.error("Apify run failed or returned no dataset")
            return []

        dataset_id = run["defaultDatasetId"]
        logger.info(f"   Apify run complete. Dataset: {dataset_id}")

        # ── PROCESS RESULTS ──
        all_leads = []
        seen_place_ids: Set[str] = set()  # FIX #1: dedup tracking
        
        stats = {
            "total_raw": 0,
            "skipped_duplicate": 0,
            "skipped_closed": 0,
            "skipped_not_beauty": 0,
            "skipped_low_reviews": 0,
            "skipped_high_reviews": 0,
            "accepted": 0,
        }
        
        for item in client.dataset(dataset_id).iterate_items():
            stats["total_raw"] += 1
            
            # ── FIX #1: DEDUPLICATION ──
            place_id = item.get("placeId")
            if place_id and place_id in seen_place_ids:
                stats["skipped_duplicate"] += 1
                continue
            if place_id:
                seen_place_ids.add(place_id)
            
            # ── Skip closed businesses ──
            if item.get("permanentlyClosed") or item.get("temporarilyClosed"):
                stats["skipped_closed"] += 1
                continue
            
            # ── FIX #2: CATEGORY FILTER ──
            if not _is_industry_related(item, industry):
                stats["skipped_not_beauty"] += 1
                logger.debug(f"   Skipped (not {industry}): {item.get('title')} | Categories: {item.get('categoryName')}, {item.get('categories')}")
                continue
            
            # ── SWEET SPOT FILTER ──
            # Use 'or 0' and check type to avoid NoneType/Dict errors
            raw_count = item.get("reviewsCount")
            if isinstance(raw_count, dict):
                # Actor fix for rare nested records
                review_count = int(raw_count.get("count", raw_count.get("total", 0)))
            else:
                try:
                    review_count = int(raw_count or 0)
                except (ValueError, TypeError):
                    review_count = 0
            
            min_rev = settings.REVIEW_MIN
            max_rev = settings.REVIEW_MAX
            
            # Solar leads often have more reviews than local salons
            if industry == "solar":
                max_rev = 1000
                
            if review_count < min_rev:
                stats["skipped_low_reviews"] += 1
                logger.debug(f"   Skipped (low reviews: {review_count}): {item.get('title')}")
                continue
            if review_count > max_rev:
                stats["skipped_high_reviews"] += 1
                logger.debug(f"   Skipped (high reviews: {review_count}): {item.get('title')}")
                continue

            # ── MAP TO LEAD PROFILE ──
            lead = _map_apify_item_to_lead(item, industry)
            if not lead:
                continue
            
            # ── FIX #4: NORMALIZE PHONE ──
            _normalize_phone(lead)
            
            # ── INITIAL CLASSIFICATION & SCORING ──
            lead.category = _classify_from_maps_data(lead)
            lead.lead_score = _compute_initial_lead_score(lead)
            
            all_leads.append(lead)
            stats["accepted"] += 1
            
            if max_results and len(all_leads) >= max_results:
                logger.info(f"   Reached requested max_results ({max_results}). ")
                break

        # ── LOG STATS ──
        logger.info(f"✅ Discovery complete:")
        for key, val in stats.items():
            logger.info(f"   {key}: {val}")
        
        return all_leads

    except Exception as e:
        logger.error(f"❌ Apify Discovery failed: {str(e)}")
        raise

async def enrich_leads(
    leads: List[LeadProfile],
    industry: str = "salon",
    include_reviews: bool = True,
    max_reviews: int = 10,
) -> List[LeadProfile]:
    """
    Takes a list of lead skeletons (e.g. from Excel) and enriches them 
    with deep Google Maps data (Reviews, Ratings, Place ID).
    
    Uses "Search strings" mode in Apify to find exact matches.
    """
    if not leads:
        return []

    logger.info(f"🚀 Starting Apify Enrichment for {len(leads)} leads")
    
    # We use a smaller queries list for targeted matching
    # Format: "Lead Name, City, State"
    query_strings = []
    for s in leads:
        query = f"{s.name}"
        if s.city: query += f", {s.city}"
        if s.address: query += f", {s.address}"
        query_strings.append(query)

    client = ApifyClient(settings.APIFY_TOKEN)

    # For enrichment, we only want the TOP 1 match for each search string
    run_input = {
        "searchStringsArray": query_strings,
        "maxCrawledPlacesPerSearch": 1, 
        "language": "en",
        "proxyConfiguration": {"useApifyProxy": True},
        "skipClosedPlaces": False, # Keep them so we can flag them as DEAD/LOGIC instead of just missing
        "maxReviews": max_reviews if include_reviews else 0,
        "reviewsSort": "newest",
    }

    try:
        run = client.actor(ACTOR_ID).call(run_input=run_input)
        dataset_id = run["defaultDatasetId"]
        
        enriched_results = []
        seen_place_ids = set()

        for item in client.dataset(dataset_id).iterate_items():
            place_id = item.get("placeId")
            if place_id in seen_place_ids: continue
            if place_id: seen_place_ids.add(place_id)

            enriched_lead = _map_apify_item_to_lead(item, industry)
            if enriched_lead:
                _normalize_phone(enriched_lead)
                enriched_lead.category = _classify_from_maps_data(enriched_lead)
                enriched_lead.lead_score = _compute_initial_lead_score(enriched_lead)
                enriched_results.append(enriched_lead)

        logger.info(f"✅ Enrichment complete. Found {len(enriched_results)}/{len(leads)} matches.")
        return enriched_results

    except Exception as e:
        logger.error(f"❌ Apify Enrichment failed: {str(e)}")
        return leads # Return original skeletons on total failure


# ─────────────────────────────────────────────
# FIX #2: INDUSTRY CATEGORY FILTER
# ─────────────────────────────────────────────

def _is_industry_related(item: Dict[str, Any], industry: str) -> bool:
    """
    Check if the Google Maps listing matches the target industry.
    """
    # Check primary category
    primary = (item.get("categoryName") or "").lower()
    all_cats = [c.lower() for c in (item.get("categories") or [])]
    
    # Also check the business name as a fallback
    name = (item.get("title") or "").lower()
    
    searchable = primary + " " + " ".join(all_cats) + " " + name
    
    keywords = INDUSTRY_CATEGORY_KEYWORDS.get(industry, INDUSTRY_CATEGORY_KEYWORDS["salon"])
    return any(kw in searchable for kw in keywords)


# ─────────────────────────────────────────────
# FIX #4: PHONE NORMALIZATION
# ─────────────────────────────────────────────

def _normalize_phone(lead: LeadProfile):
    """
    Normalize phone numbers globally using country_code.
    Handles US (+1), India (+91), etc.
    """
    raw = lead.phone_unformatted or lead.phone
    if not raw:
        return
    
    # Strip everything except digits and +
    digits = US_PHONE_CLEAN_REGEX.sub('', raw)
    
    # CASE 1: Already has a plus (e.g. +91...)
    if digits.startswith("+"):
        lead.phone_unformatted = digits
        return

    # CASE 2: 10-digit number (common in US and India)
    if len(digits) == 10:
        if lead.country_code == "IN":
            lead.phone_unformatted = "+91" + digits
        else:
            # Default to US (+1) for legacy or unknown 10-digit
            lead.phone_unformatted = "+1" + digits
        return

    # CASE 3: US format without plus
    if digits.startswith("1") and len(digits) == 11:
        lead.phone_unformatted = "+" + digits
        return

    # CASE 4: India format without plus
    if digits.startswith("91") and len(digits) == 12:
        lead.phone_unformatted = "+" + digits
        return

    # FALLBACK: Just add the plus to whatever we have
    lead.phone_unformatted = "+" + digits


# ─────────────────────────────────────────────
# DATA MAPPING (Apify JSON → LeadProfile)
# ─────────────────────────────────────────────

def _map_apify_item_to_lead(item: Dict[str, Any], industry: str) -> Optional[LeadProfile]:
    """Maps a raw Apify result to our LeadProfile model."""
    try:
        # Reviews
        reviews = []
        for r in (item.get("reviews") or []):
            reviews.append(Review(
                reviewer_name=r.get("name"),
                text=r.get("text"),
                stars=r.get("stars"),
                published_at=r.get("publishedAtDate"),
            ))

        # Review distribution
        dist = item.get("reviewsDistribution") or {}
        review_dist = ReviewDistribution(
            one_star=dist.get("1") or dist.get("oneStar", 0),
            two_star=dist.get("2") or dist.get("twoStar", 0),
            three_star=dist.get("3") or dist.get("threeStar", 0),
            four_star=dist.get("4") or dist.get("fourStar", 0),
            five_star=dist.get("5") or dist.get("fiveStar", 0),
        )

        # Opening hours
        hours = []
        for h in (item.get("openingHours") or []):
            hours.append(OpeningHoursEntry(
                day=h.get("day", ""),
                hours=h.get("hours", ""),
            ))

        # Booking/order links
        booking_links = [bl for bl in (item.get("bookingLinks") or []) if isinstance(bl, dict)]
        order_links = [ol for ol in (item.get("orderBy") or []) if isinstance(ol, dict)]
        review_tags = item.get("reviewsTags") or []

        # ── NUMERIC HARDENING (Pre-cleaning) ──
        raw_rating = item.get("totalScore")
        if isinstance(raw_rating, dict):
            google_rating = float(raw_rating.get("total", raw_rating.get("value", 0.0)))
        else:
            google_rating = float(raw_rating or 0.0)

        raw_count = item.get("reviewsCount")
        if isinstance(raw_count, dict):
            google_review_count = int(raw_count.get("count", raw_count.get("total", 0)))
        else:
            google_review_count = int(raw_count or 0)

        return LeadProfile(
            name=item.get("title", "Unknown"),
            industry=industry,
            place_id=item.get("placeId"),
            google_maps_url=item.get("url"),
            google_category=item.get("categoryName"),
            all_categories=item.get("categories") or [],
            phone=item.get("phone"),
            phone_unformatted=item.get("phoneUnformatted"),
            address=item.get("address"),
            street=item.get("street"),
            city=item.get("city"),
            state=item.get("state"),
            zip_code=item.get("postalCode"),
            country_code=item.get("countryCode", "US"),
            neighborhood=item.get("neighborhood"),
            website_url=item.get("website"),
            google_rating=google_rating,
            google_review_count=google_review_count,
            reviews_distribution=review_dist,
            reviews=reviews,
            review_tags=review_tags,
            reserve_table_url=item.get("reserveTableUrl"),
            booking_links=booking_links,
            order_links=order_links,
            permanently_closed=item.get("permanentlyClosed", False),
            temporarily_closed=item.get("temporarilyClosed", False),
            claim_this_business=item.get("claimThisBusiness", False),
            opening_hours=hours,
            scraped_at=item.get("scrapedAt"),
            image_url=item.get("imageUrl"),
        )

    except Exception as e:
        logger.warning(f"Failed to map Apify item: {str(e)}")
        return None


# ─────────────────────────────────────────────
# INITIAL CLASSIFICATION (from Maps data only)
# ─────────────────────────────────────────────

def _classify_from_maps_data(lead: LeadProfile) -> LeadCategory:
    """Pre-Playwright classification using ONLY Google Maps data."""
    if not lead.website_url:
        return LeadCategory.NO_WEBSITE
    
    booking_platform = _detect_booking_from_maps_links(lead)
    if booking_platform:
        lead.booking_system = booking_platform
        return LeadCategory.FULLY_AUTOMATED
    
    return LeadCategory.STATIC_WEBSITE


def _detect_booking_from_maps_links(lead: LeadProfile) -> Optional[str]:
    """Check if Google Maps booking/reservation links point to a known platform."""
    urls_to_check = []
    
    if lead.reserve_table_url:
        urls_to_check.append(lead.reserve_table_url)
    
    for link in lead.booking_links:
        url = link.get("url") or link.get("orderUrl") or ""
        urls_to_check.append(url)

    for link in lead.order_links:
        url = link.get("url") or link.get("orderUrl") or ""
        urls_to_check.append(url)

    for url in urls_to_check:
        url_lower = url.lower()
        for domain in KNOWN_AUTOMATION_DOMAINS:
            if domain in url_lower:
                return domain.split(".")[0].capitalize()
    
    return None


# ─────────────────────────────────────────────
# INITIAL LEAD SCORING (from Maps data only)
# ─────────────────────────────────────────────

def _compute_initial_lead_score(lead: LeadProfile) -> int:
    """Compute initial lead quality score (0-100) from Maps data."""
    score = 0
    breakdown = {}
    
    if not lead.website_url:
        score += 20
        breakdown["no_website"] = 20
        # Additional bonus for absolute focus on maps
        score += 10
        breakdown["no_website_bonus"] = 10
    
    if lead.claim_this_business:
        score += 15
        breakdown["unclaimed_listing"] = 15
    
    # "Ideal" review count (the user's sweet spot)
    if 25 <= lead.google_review_count <= 80:
        score += 15
        breakdown["ideal_review_count"] = 15
    elif 20 <= lead.google_review_count < 25:
        score += 10
        breakdown["good_review_count"] = 10
    
    # "Improvement Potential" (The sweet spot for low ratings)
    if lead.google_rating and 3.5 <= lead.google_rating <= 4.3:
        score += 10
        breakdown["improvement_potential"] = 10
    
    # "Phone Dependent" bonus
    if lead.phone and not lead.website_url:
        score += 10
        breakdown["phone_dependent"] = 10
    
    complaint_keywords = ["wait", "phone", "appointment", "booking", "hold", "rude", "slow", "delay", "scam", "pushy"]
    for tag in lead.review_tags:
        title = (tag.get("title") or "").lower()
        if any(kw in title for kw in complaint_keywords):
            score += 5
            breakdown[f"complaint_tag_{title}"] = 5
    
    score = max(0, min(100, score))
    lead.lead_score_breakdown = breakdown
    return score
    
    return score
