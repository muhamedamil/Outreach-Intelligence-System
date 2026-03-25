from pydantic import BaseModel, HttpUrl
from typing import Optional, List


class SizeSignals(BaseModel):
    """
    Rough indicators of business size.
    We don't always get hard numbers, so these are estimates — treat them as signals, not facts.
    """
    employee_estimate: Optional[str] = None
    branches: Optional[str] = None


class DigitalPresence(BaseModel):
    """
    Where the business lives online.
    A missing website in 2025 is itself a signal worth noting.
    """
    website: Optional[HttpUrl] = None
    social_links: List[HttpUrl] = []


class ToolsDetected(BaseModel):
    """
    Software stack we could sniff out from their site or listings.
    Useful for qualifying leads — a business on Calendly is very different from one taking calls on WhatsApp.
    """
    booking_system: Optional[str] = None
    crm: Optional[str] = None
    communication: Optional[str] = None


class Source(BaseModel):
    """
    Where a particular piece of data came from.
    Not all sources are equal — a verified Google listing beats a JustDial scrape any day.
    reliability is a 0–1 float we assign based on how much we trust that source.
    """
    type: str  # website, google, justdial, etc.
    url: Optional[HttpUrl] = None
    reliability: float = 0.5  # 0–1


class BusinessProfile(BaseModel):
    """
    The main output of the scraping pipeline — everything we know about a business, stitched together.

    company_name and location are the only fields we always expect.
    Everything else is best-effort. confidence_score tells you how much to trust the overall profile.
    Don't treat a low-confidence profile as ground truth — flag it for review or re-scrape.
    """
    company_name: str
    location: str
    industry: Optional[str] = None
    description: Optional[str] = None
    size_signals: SizeSignals = SizeSignals()
    digital_presence: DigitalPresence = DigitalPresence()
    tools_detected: ToolsDetected = ToolsDetected()
    sources: List[Source] = []
    confidence_score: float = 0.0