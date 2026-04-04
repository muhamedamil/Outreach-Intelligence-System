# app/agents/outreach_writer/agent.py
#
# Layer 4: The Closer (Personalized Messaging)
#
# Generates high-converting messages for WhatsApp and Instagram DMs
# based on the technical and emotional data collected in Layers 1-3.

import logging
from typing import Dict, Any, List
from app.models.lead import LeadProfile, LeadCategory
from app.services.llm.client import llm_generate

logger = logging.getLogger(__name__)

INDUSTRY_CONFIG = {
    "salon": {
        "niche": "Beauty Niche",
        "focus_no_website": "Digital Storefront for salons",
        "focus_static": "Booking Friction (the automation angle for appointments)",
        "fallback_ig": "Love your work in {city}! Quick question regarding your bookings...",
    },
    "solar": {
        "niche": "Solar and Renewable Energy Niche",
        "focus_no_website": "Trust-building Digital Presence for contractors",
        "focus_static": "Lead Capture Friction (the quote generation angle)",
        "fallback_ig": "Hey! We help solar teams in {city} close more deals. Quick question...",
    }
}

async def generate_lead_outreach(lead: LeadProfile) -> Dict[str, str]:
    """
    Generates personalized outreach messages for multiple channels.
    """
    logger.info(f"[{lead.name}] Generating multi-channel outreach...")

    # 1. Extract the "Hook" and "Insights" from Layer 3
    insights = lead.ai_research_insights or {}
    pain_points = insights.get("pain_points", [])
    sparks = insights.get("sparks", [])
    hook = insights.get("personalization_hook", "")
    recommended_angle = insights.get("recommended_angle", "automation")

    # 2. Build the Context for the LLM
    context = f"""
    Lead Name: {lead.name}
    City: {lead.city}
    Category: {lead.category} (Current State)
    WhatsApp: {lead.whatsapp_status}
    Google Rating: {lead.google_rating} ({lead.google_review_count} reviews)
    
    Emotional Insights:
    - Pain Points: {', '.join(pain_points)}
    - Sparks: {', '.join(sparks)}
    - AI Suggested Hook: {hook}
    """

    ind_cfg = INDUSTRY_CONFIG.get(lead.industry, INDUSTRY_CONFIG["salon"])

    system_prompt = f"""
    You are a world-class Sales Copywriter specializing in the {ind_cfg['niche']}.
    Generate two high-converting, friendly, and non-spammy outreach drafts.
    
    ANGLE: {recommended_angle}
    
    Draft 1: WHATSAPP (Direct, casual, professional, uses emojis, under 100 words).
    Draft 2: INSTAGRAM DM (Social, visual-first, shorter, focus on current profile).
    
    RULES:
    - Mention a SPECIFIC detail from the 'Sparks' or 'Pain Points' to prove you researched them.
    - If they have NO website, focus on "{ind_cfg['focus_no_website']}".
    - If they have a STATIC website, focus on "{ind_cfg['focus_static']}".
    - Call to action: Ask a simple question to start a conversation.
    """

    try:
        llm_output = await llm_generate(context, system_prompt=system_prompt)
        
        # We'll split the output simply for now or use a JSON format
        return {
            "whatsapp": llm_output.split("Draft 2:")[0].replace("Draft 1:", "").strip(),
            "instagram": llm_output.split("Draft 2:")[-1].strip(),
            "selected_angle": recommended_angle
        }

    except Exception as e:
        logger.error(f"[{lead.name}] Outreach generation failed: {str(e)}")
        city_str = lead.city or "your area"
        return {
            "whatsapp": f"Hi {lead.name}, I'm reaching out from Outreach AI. Would love to chat about your systems!",
            "instagram": ind_cfg["fallback_ig"].format(city=city_str),
            "selected_angle": "fallback"
        }