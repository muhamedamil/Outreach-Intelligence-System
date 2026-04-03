# app/models/lead.py
#
# "Top 1%" Data Model for Universal Lead Intelligence (Salon & Solar).
# Built to match the EXACT output schema of compass/crawler-google-places
# and enriched by our local Playwright website analyzer.

from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Dict, Any, Union
from enum import Enum


# ─────────────────────────────────────────────
# ENUMS
# ─────────────────────────────────────────────

class LeadCategory(str, Enum):
    STATIC_WEBSITE = "STATIC_WEBSITE"       # 🟡 Has site, no tracking/automation
    NO_WEBSITE = "NO_WEBSITE"               # 🔴 No website found at all
    FULLY_AUTOMATED = "FULLY_AUTOMATED"     # 🔵 Has integrated automation (Booking/Quoter)

class WhatsAppStatus(str, Enum):
    DETECTED = "DETECTED"
    NOT_DETECTED = "NOT_DETECTED"
    UNVERIFIED = "UNVERIFIED"

class WebsiteStatus(str, Enum):
    LIVE = "LIVE"                           # Site loads normally
    DEAD = "DEAD"                           # DNS not resolved / connection refused
    PARKED = "PARKED"                       # GoDaddy/Sedo parked domain
    UNDER_CONSTRUCTION = "UNDER_CONSTRUCTION"  # "Coming soon" page
    CLOUDFLARE_BLOCKED = "CLOUDFLARE_BLOCKED"  # Bot protection challenge
    TIMEOUT = "TIMEOUT"                     # Site too slow to load
    ERROR = "ERROR"                         # HTTP 4xx/5xx
    NONE = "NONE"                           # No URL provided


# ─────────────────────────────────────────────
# SUB-MODELS (for nested Apify data)
# ─────────────────────────────────────────────

class ReviewDistribution(BaseModel):
    """Star rating breakdown from Google Maps."""
    one_star: int = 0
    two_star: int = 0
    three_star: int = 0
    four_star: int = 0
    five_star: int = 0
    
    @field_validator("*", mode="before")
    @classmethod
    def ensure_int(cls, v: Any) -> int:
        if isinstance(v, dict):
            return int(v.get("count", v.get("value", 0)))
        try:
            return int(v or 0)
        except (ValueError, TypeError):
            return 0


class Review(BaseModel):
    """Individual Google review — used for sentiment analysis."""
    reviewer_name: Optional[str] = None
    text: Optional[str] = None
    stars: Optional[int] = None
    
    @field_validator("stars", mode="before")
    @classmethod
    def ensure_int(cls, v: Any) -> Optional[int]:
        if v is None: return None
        if isinstance(v, dict):
            return int(v.get("count", v.get("value", 0)))
        try:
            return int(v)
        except (ValueError, TypeError):
            return None
    published_at: Optional[str] = None


class OpeningHoursEntry(BaseModel):
    """Single day's opening hours."""
    day: str
    hours: str


# ─────────────────────────────────────────────
# CORE LEAD MODEL
# ─────────────────────────────────────────────

class LeadProfile(BaseModel):
    """
    The complete Universal Lead intelligence model.
    
    Data sources:
        - Apify (compass/crawler-google-places): name, phone, address, 
          website, rating, reviews, categories, opening hours, 
          booking links, reservation URLs, Google Maps URL, place_id
        - Playwright (website_analyzer.py): category determination, 
          WhatsApp detection, social media extraction, lead scoring
    """

    # ── IDENTITY (from Apify) ──
    name: str
    industry: str = "salon"  # Default to salon, can be set to "solar"
    place_id: Optional[str] = None
    google_maps_url: Optional[str] = None
    google_category: Optional[str] = None
    all_categories: List[str] = []

    # ── CONTACT (from Apify + Playwright backup) ──
    phone: Optional[str] = None
    phone_unformatted: Optional[str] = None
    address: Optional[str] = None
    street: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    country_code: Optional[str] = "US"
    neighborhood: Optional[str] = None

    # ── DIGITAL PRESENCE (from Apify) ──
    website_url: Optional[str] = None
    website_status: WebsiteStatus = WebsiteStatus.NONE
    
    # ── RATINGS & REVIEWS (from Apify) ──
    google_rating: Optional[float] = None
    google_review_count: int = 0
    reviews_distribution: Optional[ReviewDistribution] = None
    reviews: List[Review] = []
    review_tags: List[Dict[str, Any]] = []

    # ── BOOKING SIGNALS (from Apify + Playwright) ──
    reserve_table_url: Optional[str] = None
    booking_links: List[Dict[str, str]] = []
    order_links: List[Dict[str, str]] = []

    # ── THE "3 BUCKETS" LOGIC ──
    category: LeadCategory = LeadCategory.NO_WEBSITE
    booking_system: Optional[str] = None

    # ── WHATSAPP & SOCIAL (from Playwright) ──
    whatsapp_status: WhatsAppStatus = WhatsAppStatus.UNVERIFIED
    whatsapp_number: Optional[str] = None
    instagram_url: Optional[str] = None
    facebook_url: Optional[str] = None
    tiktok_url: Optional[str] = None
    youtube_url: Optional[str] = None
    yelp_url: Optional[str] = None

    # ── BUSINESS STATUS (from Apify) ──
    permanently_closed: bool = False
    temporarily_closed: bool = False
    claim_this_business: bool = False
    opening_hours: List[OpeningHoursEntry] = []

    # ── LEAD QUALITY (computed by our system) ──
    negative_review_signals: List[str] = []
    lead_score: int = 0
    lead_score_breakdown: Dict[str, int] = {}

    # ── METADATA ──
    scraped_at: Optional[str] = None
    image_url: Optional[str] = None

    model_config = {
        "validate_assignment": True,
        "populate_by_name": True
    }

    # ─────────────────────────────────────────────
    # PRO-GRADE HARDENING: Advanced Field Validators
    # (Captures edge cases where APIs return dicts instead of ints)
    # ─────────────────────────────────────────────

    @field_validator("google_rating", "google_review_count", "lead_score", mode="before")
    @classmethod
    def ensure_numeric(cls, v: Any, info: Any) -> Union[int, float, None]:
        """Strictly cast numeric fields to resolve 'Expected int but got dict' warnings."""
        field_name = info.field_name
        
        if v is None:
            # Counts and scores default to 0, ratings to None
            return 0 if "count" in field_name or "score" in field_name else None
        
        # If the input is a dictionary (common Apify actor bug)
        if isinstance(v, dict):
            # Try to extract common keys like 'count', 'value', 'score', 'total', 'rating', or the field name itself
            for key in ["count", "value", "score", "total", "rating", "totalScore", field_name]:
                if key in v:
                    val = v[key]
                    # Recursive check just in case it's a dict within a dict
                    return cls.ensure_numeric(val, info)
            return 0 # Fallback for unknown dict structure
            
        try:
            # Cast based on field type
            if field_name == "google_rating":
                return float(v)
            return int(float(v)) # Handle "10.0" as string
        except (ValueError, TypeError):
            return 0 if "count" in field_name or "score" in field_name else None

    @field_validator("lead_score_breakdown", mode="before")
    @classmethod
    def ensure_valid_breakdown(cls, v: Any) -> Dict[str, int]:
        """Ensure lead_score_breakdown is always a clean Dict[str, int]."""
        if not isinstance(v, dict):
            return {}
        
        # Clean the values to ensure they are all integers
        cleaned = {}
        for key, value in v.items():
            if isinstance(value, dict):
                # Another nested dict edge case
                cleaned[str(key)] = int(value.get("value", value.get("count", 0)))
            else:
                try:
                    cleaned[str(key)] = int(value)
                except (ValueError, TypeError):
                    cleaned[str(key)] = 0
        return cleaned
