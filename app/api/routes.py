from typing import Optional, List, Any, Dict
import time
from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from pydantic import BaseModel
from app.api.schemas import BatchResponse
from app.utils.logger import get_logger
from app.config.settings import settings

router = APIRouter()
logger = get_logger(__name__)

MAX_FILE_SIZE = 5 * 1024 * 1024


# ── Request Models ──
class SingleOutreachRequest(BaseModel):
    lead: Dict[str, Any]

class BatchOutreachRequest(BaseModel):
    leads: List[Dict[str, Any]]


# ── Health ──
@router.get("/health")
async def health():
    return {"status": "ok"}


# ── PHASE 1: Discovery Campaign (Layers 1–3, no outreach) ──
@router.post("/campaign", response_model=BatchResponse)
async def run_campaign_api(
    prompt: Optional[str] = Form(None),
    limit: int = Form(10),
    file: Optional[UploadFile] = File(None),
    seen_leads_file: Optional[UploadFile] = File(None)  # Dedup: previously exported leads CSV
):
    start_time = time.time()
    logger.info(f"Campaign API triggered -> Prompt: {prompt}, File: {file.filename if file else 'None'}")

    if not prompt and not file:
        raise HTTPException(status_code=400, detail="Must provide either a text prompt or an excel file.")

    try:
        raw_results = []

        # ── CASE 1: File-only → Enrichment mode ──
        if file and not prompt:
            if not file.filename.endswith((".xlsx", ".xls", ".csv")):
                raise HTTPException(status_code=400, detail="Invalid file type")
            from app.services.file_processor.excel_processor import process_excel_content
            contents = await file.read()
            if len(contents) > MAX_FILE_SIZE:
                raise HTTPException(status_code=400, detail="File too large")
            raw_results = await process_excel_content(contents, file.filename, limit=limit)

        # ── CASE 2: Prompt (+ optional seen_leads_file for dedup) → Discovery mode ──
        else:
            from app.services.campaign.campaign_manager import run_discovery_campaign
            from app.services.dedup.csv_dedup import build_seen_leads_index, SeenLeadsIndex

            # Build the dedup index from the seen-leads file if provided
            seen_index: Optional[SeenLeadsIndex] = None
            if seen_leads_file:
                if not seen_leads_file.filename.endswith((".xlsx", ".xls", ".csv")):
                    raise HTTPException(status_code=400, detail="seen_leads_file must be a CSV or Excel file.")
                seen_bytes = await seen_leads_file.read()
                if len(seen_bytes) > MAX_FILE_SIZE:
                    raise HTTPException(status_code=400, detail="seen_leads_file too large.")
                seen_index = build_seen_leads_index(seen_bytes, seen_leads_file.filename)
                logger.info(f"Dedup index loaded: {seen_index.total_seen} previously seen leads.")

            raw_results = await run_discovery_campaign(
                prompt=prompt,
                limit=limit,
                seen_index=seen_index
            )


        formatted_results = []
        for i, res in enumerate(raw_results):
            lead = res["lead"]
            business_data = lead.model_dump() if hasattr(lead, "model_dump") else lead
            formatted_results.append({
                "status": "success",
                "business_profile": business_data,
                "outreach": None,   # Outreach is generated on-demand via /api/outreach/*
                "row_id": i + 1
            })

        total_time = round((time.time() - start_time), 2)
        from app.utils.response_formatter import format_final_response
        response = format_final_response(formatted_results, total_time)

        if settings.DEBUG:
            response["debug"] = {"processing_time": total_time}

        logger.info(f"Discovery pipeline completed in {total_time}s")
        return response

    except Exception as e:
        logger.error(f"Campaign API failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Processing failed: {str(e)}")


# ── PHASE 2a: Generate outreach for a SINGLE lead ──
@router.post("/outreach/single")
async def generate_single_outreach(payload: SingleOutreachRequest):
    """
    Accepts a serialized LeadProfile dict, runs Layer 4 (outreach writer),
    returns the generated outreach drafts.
    """
    logger.info(f"Single outreach request for: {payload.lead.get('name', 'Unknown')}")
    try:
        from app.services.campaign.campaign_manager import run_outreach_for_lead
        outreach = await run_outreach_for_lead(payload.lead)
        return {"success": True, "outreach": outreach}
    except Exception as e:
        logger.error(f"Single outreach API failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Outreach generation failed: {str(e)}")


# ── PHASE 2b: Generate outreach for ALL/MULTIPLE leads (batch) ──
@router.post("/outreach/batch")
async def generate_batch_outreach(payload: BatchOutreachRequest):
    """
    Accepts a list of serialized LeadProfile dicts, runs Layer 4 for each,
    returns list of {name, outreach, status}.
    """
    logger.info(f"Batch outreach request for {len(payload.leads)} leads")
    try:
        from app.services.campaign.campaign_manager import run_outreach_for_batch
        results = await run_outreach_for_batch(payload.leads)
        return {"success": True, "results": results, "total": len(results)}
    except Exception as e:
        logger.error(f"Batch outreach API failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Batch outreach failed: {str(e)}")
