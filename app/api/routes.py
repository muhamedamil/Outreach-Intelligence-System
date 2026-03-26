import os
import time
import uuid
import asyncio
from fastapi import APIRouter, UploadFile, File, HTTPException

from app.services.file_processor.excel_processor import process_excel
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


@router.post("/upload", response_model=BatchResponse)
async def upload_file(file: UploadFile = File(...)):

    start_time = time.time()
    logger.info("Upload request started")

    if not file.filename.endswith((".xlsx", ".xls")):
        raise HTTPException(status_code=400, detail="Invalid file type")

    temp_path = f"temp_{uuid.uuid4()}.xlsx"

    try:
        contents = await file.read()

        if len(contents) > MAX_FILE_SIZE:
            raise HTTPException(status_code=400, detail="File too large")

        with open(temp_path, "wb") as f:
            f.write(contents)

        logger.info(f"File uploaded: {file.filename}")

        # Timeout protection
        results = await asyncio.wait_for(process_excel(temp_path), timeout=3600)

        total_time = round((time.time() - start_time), 2)

        response = format_final_response(results, total_time)

        # Optional debug info
        if settings.DEBUG:
            response["debug"] = {"processing_time": total_time}

        logger.info(f"Processing completed in {total_time}s")

        return response

    except ValueError as e:
        logger.error(f"Validation error: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))

    except asyncio.TimeoutError:
        logger.error("Processing timeout")
        raise HTTPException(status_code=504, detail="Processing timeout")

    except Exception as e:
        logger.error(f"Processing failed: {str(e)}")
        raise HTTPException(status_code=500, detail="Processing failed")

    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)
