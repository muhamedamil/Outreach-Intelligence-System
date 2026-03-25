# app/agents/outreach_writer/prompt.py

def build_outreach_prompt(profile, has_contact: bool) -> str:
    return f"""
You are writing a cold outreach message for a business.

Business Info:
- Name: {profile.company_name}
- Industry: {profile.industry}
- Description: {profile.description}

Instructions:
- Write a short WhatsApp-style message (3–5 lines max)
- Focus on outcome/value, not introduction
- Make it sound natural, not salesy
- No buzzwords, no generic phrases
- Keep it concise and specific

{"Mention that their contact details were not publicly available." if not has_contact else ""}

Context:
We build AI systems like:
- Voice receptionists
- Workflow automation
- Custom SaaS tools for SMBs

Return ONLY the message text.
"""