# app/models/lead.py
#
# "Top 1%" Data Model for Universal Lead Intelligence (Salon & Solar).
# Built to match the EXACT output schema of compass/crawler-google-places
# and enriched by our local Playwright website analyzer.
#
# PYDANTIC 2.7.4 HARDENING STRATEGY (three layers):
#
#   Layer 1 — model_validator(mode="before")
#     Cleans raw input data BEFORE field assignment. Handles 90% of cases.
#
#   Layer 2 — field_validator(mode="before") with EXPLICIT field names
#     Per-field safety net. NEVER use wildcard "*" — unreliable in 2.7.x.
#
#   Layer 3 — field_serializer(when_used="always")
#     The final guarantee: forces correct types AT SERIALIZATION TIME.
#     This catches anything that slips through layers 1 & 2 (e.g. direct
#     dict-item assignments like lead.lead_score_breakdown["x"] = v which
#     Pydantic's validate_assignment does NOT intercept).

from pydantic import BaseModel, field_validator, model_validator, field_serializer
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
# SHARED HELPERS
# ─────────────────────────────────────────────

def _to_int(v: Any, default: int = 0) -> int:
    """Safely cast any Apify value to int — handles None, dicts, strings, floats."""
    if v is None:
        return default
    if isinstance(v, dict):
        for key in ("count", "value", "total", "stars", "score"):
            if key in v:
                return _to_int(v[key], default)
        return default
    try:
        return int(float(v))
    except (ValueError, TypeError):
        return default


def _to_opt_float(v: Any) -> Optional[float]:
    """Safely cast any Apify value to Optional[float]."""
    if v is None:
        return None
    if isinstance(v, dict):
        for key in ("value", "total", "rating", "score"):
            if key in v:
                return _to_opt_float(v[key])
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _clean_int_dict(v: Any) -> Dict[str, int]:
    """Ensure a dict has str keys and int values."""
    if not isinstance(v, dict):
        return {}
    cleaned: Dict[str, int] = {}
    for k, val in v.items():
        cleaned[str(k)] = _to_int(val)
    return cleaned


# ─────────────────────────────────────────────
# SUB-MODELS
# ─────────────────────────────────────────────

class ReviewDistribution(BaseModel):
    """Star rating breakdown from Google Maps."""
    one_star: int = 0
    two_star: int = 0
    three_star: int = 0
    four_star: int = 0
    five_star: int = 0

    model_config = {"validate_assignment": True}

    # ── Layer 1: Pre-clean ALL incoming data before field assignment ──
    @model_validator(mode="before")
    @classmethod
    def _pre_clean(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        return {k: _to_int(v) for k, v in data.items()}

    # ── Layer 2: Explicit per-field validators (no wildcard) ──
    @field_validator("one_star", "two_star", "three_star", "four_star", "five_star", mode="before")
    @classmethod
    def _ensure_int(cls, v: Any) -> int:
        return _to_int(v)

    # ── Layer 3: Force correct type at serialization — THE KEY FIX ──
    @field_serializer("one_star", "two_star", "three_star", "four_star", "five_star", when_used="always")
    def _serialize_star(self, v: Any) -> int:
        return _to_int(v)


class Review(BaseModel):
    """Individual Google review — used for sentiment analysis."""
    # ── ALL FIELDS defined BEFORE validators (required in Pydantic 2.7.x) ──
    reviewer_name: Optional[str] = None
    text: Optional[str] = None
    stars: Optional[int] = None
    published_at: Optional[str] = None

    model_config = {"validate_assignment": True}

    # ── Layer 2: stars validator ──
    @field_validator("stars", mode="before")
    @classmethod
    def _ensure_stars(cls, v: Any) -> Optional[int]:
        if v is None:
            return None
        return _to_int(v) or None

    # ── Layer 3: Force int at serialization ──
    @field_serializer("stars", when_used="always")
    def _serialize_stars(self, v: Any) -> Optional[int]:
        if v is None:
            return None
        return _to_int(v) or None


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
          social media extraction, lead scoring
    """

    # ── IDENTITY ──
    name: str
    industry: str = "salon"
    place_id: Optional[str] = None
    google_maps_url: Optional[str] = None
    google_category: Optional[str] = None
    all_categories: List[str] = []

    # ── CONTACT ──
    phone: Optional[str] = None
    phone_unformatted: Optional[str] = None
    address: Optional[str] = None
    street: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    country_code: Optional[str] = "US"
    neighborhood: Optional[str] = None

    # ── DIGITAL PRESENCE ──
    website_url: Optional[str] = None
    website_status: WebsiteStatus = WebsiteStatus.NONE

    # ── RATINGS & REVIEWS ──
    google_rating: Optional[float] = None
    google_review_count: int = 0
    reviews_distribution: Optional[ReviewDistribution] = None
    reviews: List[Review] = []
    review_tags: List[Dict[str, Any]] = []

    # ── BOOKING SIGNALS ──
    reserve_table_url: Optional[str] = None
    # NOTE: declared as List[Dict[str, Any]] — Apify returns mixed-type values.
    # Declaring as Dict[str, str] caused silent serialization warnings in 2.7.4.
    booking_links: List[Dict[str, Any]] = []
    order_links: List[Dict[str, Any]] = []

    # ── 3-BUCKET CLASSIFICATION ──
    category: LeadCategory = LeadCategory.NO_WEBSITE
    booking_system: Optional[str] = None

    # ── SOCIAL / CONTACT CHANNELS ──
    whatsapp_status: WhatsAppStatus = WhatsAppStatus.UNVERIFIED
    whatsapp_number: Optional[str] = None
    whatsapp_confidence: str = "LOW"
    whatsapp_found_on_web: bool = False
    instagram_url: Optional[str] = None
    facebook_url: Optional[str] = None
    tiktok_url: Optional[str] = None
    youtube_url: Optional[str] = None
    yelp_url: Optional[str] = None

    # ── BUSINESS STATUS ──
    permanently_closed: bool = False
    temporarily_closed: bool = False
    claim_this_business: bool = False
    opening_hours: List[OpeningHoursEntry] = []

    # ── LEAD QUALITY ──
    negative_review_signals: List[str] = []
    lead_score: int = 0
    lead_score_breakdown: Dict[str, int] = {}
    ai_research_insights: Optional[Dict[str, Any]] = None

    # ── METADATA ──
    scraped_at: Optional[str] = None
    image_url: Optional[str] = None

    model_config = {
        "validate_assignment": True,
        "populate_by_name": True,
    }

    # ── Layer 2: Numeric field validators ──
    @field_validator("google_review_count", "lead_score", mode="before")
    @classmethod
    def _ensure_int(cls, v: Any) -> int:
        return _to_int(v)

    @field_validator("google_rating", mode="before")
    @classmethod
    def _ensure_float(cls, v: Any) -> Optional[float]:
        return _to_opt_float(v)

    @field_validator("lead_score_breakdown", mode="before")
    @classmethod
    def _ensure_breakdown(cls, v: Any) -> Dict[str, int]:
        return _clean_int_dict(v)

    @field_validator("ai_research_insights", mode="before")
    @classmethod
    def _ensure_insights(cls, v: Any) -> Optional[Dict[str, Any]]:
        if v is None: return {}
        return v if isinstance(v, dict) else {}

    # ── Layer 3: Serialization-phase cast — catches dict-item assignments ──
    # These fire even when the value was set via lead.lead_score_breakdown["x"] = v
    # (which bypasses validate_assignment in Pydantic 2.7.x)
    @field_serializer("google_review_count", "lead_score", when_used="always")
    def _serialize_int(self, v: Any) -> int:
        return _to_int(v)

    @field_serializer("google_rating", when_used="always")
    def _serialize_float(self, v: Any) -> Optional[float]:
        return _to_opt_float(v)

    @field_serializer("lead_score_breakdown", when_used="always")
    def _serialize_breakdown(self, v: Any) -> Dict[str, int]:
        return _clean_int_dict(v)

    @field_serializer("ai_research_insights", when_used="always")
    def _serialize_insights(self, v: Any) -> Optional[Dict[str, Any]]:
        if v is None: return {}
        return v if isinstance(v, dict) else {}
