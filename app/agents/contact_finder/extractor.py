# app/agents/contact_finder/extractor.py

import re
from typing import List, Optional


PHONE_REGEX = re.compile(r"(\+91[\s\-]?\d{10}|\b\d{10}\b)")
EMAIL_REGEX = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")


def extract_phone(text: str) -> Optional[str]:
    matches = PHONE_REGEX.findall(text)
    return matches[0] if matches else None


def extract_email(text: str) -> Optional[str]:
    matches = EMAIL_REGEX.findall(text)
    return matches[0] if matches else None


def extract_whatsapp(phone: Optional[str]) -> Optional[str]:
    if phone:
        return phone  # simple assumption
    return None