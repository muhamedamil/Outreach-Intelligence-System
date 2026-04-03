from typing import Optional
import time
from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from app.services.file_processor.excel_processor import process_excel_content
from app.services.campaign.campaign_manager import run_full_campaign
from app.utils.response_formatter import format_final_response
from app.api.schemas import BatchResponse
from app.utils.logger import get_logger
from app.config.settings import settings

router = APIRouter()
logger = get_logger(__name__)

MAX_FILE_SIZE = 5 * 1024 * 1024

@router.get("/health")
async def health():
    return {"status": "ok"}

@router.post("/campaign", response_model=BatchResponse)
async def run_campaign_api(
    prompt: Optional[str] = Form(None),
    limit: int = Form(10),
    file: Optional[UploadFile] = File(None)
):
    start_time = time.time()
    logger.info(f"Campaign API triggered -> Prompt: {prompt}, File: {file.filename if file else 'None'}")

    if not prompt and not file:
        raise HTTPException(status_code=400, detail="Must provide either a text prompt or an excel file.")

    try:
        raw_results = []
        if file:
            if not file.filename.endswith((".xlsx", ".xls", ".csv")):
                raise HTTPException(status_code=400, detail="Invalid file type")
            contents = await file.read()
            if len(contents) > MAX_FILE_SIZE:
                raise HTTPException(status_code=400, detail="File too large")
            raw_results = await process_excel_content(contents, file.filename, limit=limit)
        else:
            # Mining Mode
            raw_results = await run_full_campaign(prompt=prompt, limit=limit)

        # Map new LeadProfile structure to legacy expected format for frontend compatibility
        formatted_results = []
        for i, res in enumerate(raw_results):
            lead = res["lead"]
            # model_dump() ensures deep serialization to a dict, avoiding Pydantic 'int vs dict' confusion
            business_data = lead.model_dump() if hasattr(lead, "model_dump") else lead
            
            formatted_results.append({
                "status": "success",
                "business_profile": business_data,
                "outreach": res.get("campaign_outreach"),
                "row_id": i + 1  # Safe integer increment
            })

        total_time = round((time.time() - start_time), 2)
        response = format_final_response(formatted_results, total_time)

        if settings.DEBUG:
            response["debug"] = {"processing_time": total_time}

        logger.info(f"Processing completed in {total_time}s")
        return response

    except Exception as e:
        logger.error(f"Campaign API failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Processing failed: {str(e)}")
