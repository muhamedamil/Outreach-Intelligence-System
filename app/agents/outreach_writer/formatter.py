# app/agents/outreach_writer/formatter.py

def clean_message(text: str) -> str:
    """
    Clean LLM output:
    - Strip whitespace
    - Remove quotes
    - Remove markdown/code blocks
    """

    if not text:
        return ""

    text = text.strip()

    # Remove triple backticks
    if text.startswith("```"):
        text = text.strip("`")

    # Remove surrounding quotes
    if text.startswith('"') and text.endswith('"'):
        text = text[1:-1]

    return text.strip()