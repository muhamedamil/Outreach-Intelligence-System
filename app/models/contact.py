from pydantic import BaseModel, HttpUrl
from typing import List, Optional
from enum import Enum


class ContactStatus(str, Enum):
    """
    How well did we actually do finding contact info?
    FOUND means we got something usable, PARTIAL means we scraped something but it's incomplete,
    NOT_FOUND means we came back empty — worth retrying later or flagging for manual lookup.
    """
    FOUND = "FOUND"
    PARTIAL = "PARTIAL"
    NOT_FOUND = "NOT_FOUND"


class ContactSource(BaseModel):
    """
    Tracks where a specific contact detail came from.
    Useful when phone numbers from JustDial and the business website don't match — 
    you want to know which one to trust.
    """
    type: str  # website, google, justdial, etc.
    url: Optional[HttpUrl] = None


class ContactCard(BaseModel):
    """
    Everything we know about reaching this business.

    Don't assume a phone number means WhatsApp works on it — that's a separate field for a reason.
    If status is PARTIAL, at least one field is populated but we know we're missing something.
    confidence_score reflects how reliable the overall contact data is, not just whether fields are filled.
    A scraped number with no verification should never sit at 1.0.
    """
    phone: Optional[str] = None
    email: Optional[str] = None
    whatsapp: Optional[str] = None
    sources: List[ContactSource] = []
    status: ContactStatus
    confidence_score: float = 0.0