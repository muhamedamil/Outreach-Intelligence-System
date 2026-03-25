from pydantic import BaseModel
from typing import List


class OutreachMessage(BaseModel):
    """
    The actual message we're sending out to a business.

    tone defaults to 'whatsapp' because that's where most small businesses actually respond —
    not email, not calls. personalization_factors tracks what we used to tailor the message
    (owner name, industry, city, etc.) so we can debug why something felt generic or got ignored.
    An empty personalization_factors list is a red flag — it means we sent a template and hoped for the best.
    """
    message: str
    tone: str = "whatsapp"
    personalization_factors: List[str] = []

    