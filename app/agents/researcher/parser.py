# app/agents/researcher/parser.py

import json
from typing import Any, Dict


def extract_json_block(text: str) -> str:
    """
    Extract the first valid JSON object from text.
    """
    try:
        start = text.index("{")
        end = text.rindex("}") + 1
        return text[start:end]
    except ValueError:
        return ""


def safe_parse_json(text: str) -> Dict[str, Any]:
    """
    Robust JSON parsing with fallback extraction.
    """
    if not text:
        return {}

    # First attempt
    try:
        return json.loads(text)
    except Exception:
        pass

    # Try extracting JSON block
    json_str = extract_json_block(text)

    if json_str:
        try:
            return json.loads(json_str)
        except Exception:
            pass

    return {}

