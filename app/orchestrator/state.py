from pydantic import BaseModel
from typing import Optional, List, Dict, Any

from app.models.business import BusinessProfile
from app.models.contact import ContactCard
from app.models.outreach import OutreachMessage


class PipelineState(BaseModel):
    """
    Shared state that flows through every agent in the pipeline.

    Fields populate progressively — None doesn't mean failure, it means that
    agent hasn't run yet. Check errors at the end, not mid-pipeline.
    trace is your debugging lifeline when something goes wrong in production.
    """
    input: Dict[str, Any]
    research: Optional[BusinessProfile] = None
    contact: Optional[ContactCard] = None
    outreach: Optional[OutreachMessage] = None
    errors: List[str] = []
    trace: List[Dict[str, Any]] = []