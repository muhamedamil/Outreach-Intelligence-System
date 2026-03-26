# app/agents/contact_finder/validator.py

from typing import Optional
from app.models.contact import ContactStatus


# -------------------------
# NORMALIZATION HELPERS
# -------------------------
def normalize_phone(phone: Optional[str]) -> Optional[str]:
    """
    Normalize phone numbers:
    - Remove spaces, dashes
    - Ensure +91 format if Indian number
    """

    if not phone:
        return None

    phone = phone.strip().replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    
    # Clean non-digits
    phone = "".join(filter(str.isdigit, phone))

    if len(phone) < 10:
        return None

    # Basic normalization for Indian numbers
    if phone.startswith("0") and len(phone) == 11:
        phone = "+91" + phone[1:]
    elif len(phone) == 10:
        phone = "+91" + phone
    elif phone.startswith("91") and len(phone) == 12:
        phone = "+" + phone

    return phone


def normalize_email(email: Optional[str]) -> Optional[str]:
    """
    Normalize and filter emails:
    - Lowercase, Strip
    - Blacklist generic/place-holder emails
    """
    if not email:
        return None

    email = email.strip().lower()

    # Strict Blacklist for placeholder emails
    blacklist = [
        "a@gmail.com", "test@gmail.com", "admin@gmail.com", 
        "info@gmail.com", "support@gmail.com", "xyz@gmail.com",
        "email@example.com", "[email protected]"
    ]
    
    if any(b in email for b in blacklist) or len(email) < 5:
        return None

    return email


# -------------------------
# STATUS DETERMINATION
# -------------------------
def determine_status(
    phone: Optional[str],
    email: Optional[str]
) -> ContactStatus:
    """
    Determine contact completeness.
    """

    if phone and email:
        return ContactStatus.FOUND

    if phone or email:
        return ContactStatus.PARTIAL

    return ContactStatus.NOT_FOUND


# -------------------------
# CONFIDENCE SCORING
# -------------------------
def compute_confidence(
    phone: Optional[str],
    email: Optional[str],
    sources_count: int
) -> float:
    """
    Confidence scoring based on:
    - Data availability
    - Number of sources
    """

    score = 0.0

    # Data signals
    if phone:
        score += 0.5

    if email:
        score += 0.3

    # Source signal
    if sources_count >= 3:
        score += 0.2
    elif sources_count == 2:
        score += 0.1

    return round(min(score, 1.0), 2)


# -------------------------
# FINAL VALIDATION PIPELINE
# -------------------------
def validate_contact_data(
    phone: Optional[str],
    email: Optional[str],
    sources_count: int
):
    """
    Full validation pipeline:
    - Normalize
    - Determine status
    - Compute confidence
    """

    phone = normalize_phone(phone)
    email = normalize_email(email)

    status = determine_status(phone, email)

    confidence = compute_confidence(phone, email, sources_count)

    return phone, email, status, confidence