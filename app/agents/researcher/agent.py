# app/agents/researcher/agent.py
#
# Layer 3: The Brain (Emotional Intelligence)
#
# Instead of generic search, this agent analyzes deep lead data (like reviews)
# to find "Emotional Hooks" and "Pain Points" for highly personalized outreach.

import logging
import json
import re
from typing import List, Dict, Any, Optional
from app.models.lead import LeadProfile
from app.services.llm.client import llm_generate

logger = logging.getLogger(__name__)

# --- INDUSTRY SPECIFIC PROMPT LOGIC ---
INDUSTRY_PROMPTS = {
    "salon": {
        "role": "You are a Senior Sales Researcher specializing in the Beauty Industry.",
        "pain": "1. PAIN POINTS: Anything customers complain about regarding booking, phone response, or waiting times.",
        "spark": "2. SPARKS: Anything customers LOVE (specialty services, specific staff names, atmosphere)."
    },
    "solar": {
        "role": "You are a Senior Sales Researcher specializing in the Solar and Renewable Energy Industry.",
        "pain": "1. PAIN POINTS: Anything customers complain about regarding high quotes, unresponsiveness, pushy sales tactics, or installation delays.",
        "spark": "2. SPARKS: Anything customers LOVE (professionalism, transparency, quick roof installation, saving money)."
    }
}

async def run_lead_researcher(lead: LeadProfile) -> LeadProfile:
    """
    Analyzes the lead's reviews and ratings to find specific insights.
    Returns the LeadProfile with an enriched 'ai_research_insights' field.
    """
    logger.info(f"[{lead.name}] Analyzing reviews for emotional hooks...")

    # 1. Initialize the field (Ensures it appears in JSON even if reviews are missing)
    lead.ai_research_insights = {"status": "no_reviews_to_analyze"}

    # 2. Look at top 15 reviews
    review_texts = []
    for r in lead.reviews[:15]:
        if r.text:
            stars = "⭐" * (r.stars or 5)
            review_texts.append(f"[{stars}] {r.text}")

    if not review_texts:
        logger.info(f"[{lead.name}] No review text found to analyze.")
        return lead

    context = "\n---\n".join(review_texts)
    
    # 2. Extract industry logic
    ind_cfg = INDUSTRY_PROMPTS.get(lead.industry, INDUSTRY_PROMPTS["salon"])
    
    # 3. Call LLM to extract "Pain Points" and "Sparks"
    system_prompt = f"""
    {ind_cfg['role']}
    Analyze the following Google Reviews for '{lead.name}'.
    
    Your goal is to find:
    {ind_cfg['pain']}
    {ind_cfg['spark']}
    3. THE ANGLE: Should we pitch a "New Website" or "Automation Toolkit"?
    
    Return ONLY JSON:
    {{
        "pain_points": ["point 1", "point 2"],
        "sparks": ["spark 1", "spark 2"],
        "recommended_angle": "automation" | "website",
        "personalization_hook": "A 1-sentence draft hook mentioning a specific review detail."
    }}
    """
    
    try:
        llm_output = await llm_generate(context, system_prompt=system_prompt)
        
        # Parse JSON from response
        match = re.search(r'\{.*\}', llm_output, re.DOTALL)
        if not match:
            return lead
            
        insights = json.loads(match.group())
        
        # 3. Store insights in the profile
        lead.ai_research_insights = insights
        
        # Adjust lead score based on identified pain points
        if insights.get("pain_points"):
             # Wait times or complaints mean they desperately need our systems
            bonus = min(20, len(insights["pain_points"]) * 5)
            lead.lead_score += bonus
            lead.lead_score_breakdown["friction_detected"] = bonus
            
        logger.info(f"[{lead.name}] AI Research complete. Angle: {insights.get('recommended_angle')}")
        
    except Exception as e:
        logger.error(f"[{lead.name}] AI Research failed: {str(e)}")
        
    return lead
