# app/agents/outreach_writer/agent.py

from typing import Optional, List

from app.models.business import BusinessProfile
from app.models.contact import ContactCard
from app.models.outreach import OutreachMessage

from app.agents.outreach_writer.prompt import build_outreach_prompt
from app.agents.outreach_writer.formatter import clean_message

from app.services.llm.client import llm_generate


# PERSONALIZATION SIGNALS
def extract_personalization(profile: BusinessProfile) -> List[str]:
    factors = []

    if profile.industry:
        factors.append(f"industry:{profile.industry}")

    if profile.description:
        factors.append("has_description")

    if profile.tools_detected:
        if profile.tools_detected.booking_system:
            factors.append("has_booking_system")

        if profile.tools_detected.crm:
            factors.append("uses_crm")

    if profile.digital_presence:
        if not profile.digital_presence.website:
            factors.append("no_website")

    if profile.size_signals:
        if profile.size_signals.employee_estimate:
            factors.append("team_signal")

    return factors


# FALLBACK MESSAGE
def fallback_message(profile: BusinessProfile, no_contact: bool) -> str:
    name = profile.company_name or "your business"

    message = f"Hi, came across {name}"

    if profile.industry:
        message += f" in the {profile.industry} space"

    message += ". We help businesses automate customer interactions using AI (calls, WhatsApp, workflows)."

    if no_contact:
        message += " Couldn't find your contact details publicly, so reaching out here."

    message += " Worth a quick chat?"

    return message


# SAFETY CHECK
def is_valid_message(text: str) -> bool:
    """
    Basic sanity checks for LLM output
    """

    if not text:
        return False

    if len(text) < 20:
        return False

    # Avoid overly long messages
    if len(text) > 500:
        return False

    return True


# MAIN AGENT
async def run_outreach_writer(
    profile: BusinessProfile,
    contact: Optional[ContactCard]
) -> OutreachMessage:

    # VALIDATION
    if not profile:
        return OutreachMessage(
            message="Unable to generate outreach message.",
            personalization_factors=[]
        )

    has_contact = bool(contact and contact.status.value != "NOT_FOUND")
    no_contact = not has_contact

    # BUILD PROMPT
    prompt = build_outreach_prompt(profile, has_contact)

    # LLM GENERATION
    llm_output = None

    try:
        llm_output = await llm_generate(prompt)
    except Exception:
        llm_output = None

    # OUTPUT HANDLING
    if llm_output:
        message = clean_message(llm_output)

        if not is_valid_message(message):
            message = fallback_message(profile, no_contact)
    else:
        message = fallback_message(profile, no_contact)

    # PERSONALIZATION METADATA
    factors = extract_personalization(profile)

    return OutreachMessage(
        message=message,
        tone="whatsapp",
        personalization_factors=factors
    )