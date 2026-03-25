# app/agents/researcher/prompt.py

def build_prompt(context: str, company: str, location: str) -> str:
    return f"""
You are a business intelligence extraction system.

Extract structured data about the company.

Company: {company}
Location: {location}

Context:
{context}

Return STRICT JSON with keys:
- industry
- description
- employee_estimate
- branches
- website
- social_links (list)
- booking_system
- crm
- communication

Rules:
- If unknown → null
- Do NOT hallucinate
- Only use given context
- Keep answers concise
"""