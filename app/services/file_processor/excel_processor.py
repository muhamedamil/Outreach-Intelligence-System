# app/services/file_processor/excel_processor.py

import pandas as pd
import asyncio
import time
from typing import List, Dict

from app.orchestrator.pipeline import OutreachPipeline
from app.orchestrator.state import PipelineState
from app.config.settings import settings
from app.utils.logger import get_logger


logger = get_logger(__name__)


# -------------------------
# VALIDATION
# -------------------------
REQUIRED_COLUMNS = ["company_name", "location"]


def validate_columns(df: pd.DataFrame):
    # Normalize column names: lowercase, replace spaces with underscores
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]

    # Map missing columns based on the provided sample format
    if "company_name" not in df.columns and "location" not in df.columns:
        unnamed = [c for c in df.columns if "unnamed:" in str(c)]
        if len(unnamed) >= 4:
            # Shifted format: Col 0 is empty, Col 1 is Index, Col 2 is Location, Col 3 is Company Name
            df.rename(columns={
                df.columns[2]: "location",
                df.columns[3]: "company_name"
            }, inplace=True)
        elif len(unnamed) >= 3:
            # Normal no-header format: Col 0 is Index, Col 1 is Location, Col 2 is Company Name
            df.rename(columns={
                df.columns[1]: "location",
                df.columns[2]: "company_name"
            }, inplace=True)

    missing = [col for col in REQUIRED_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}. Expected 'company_name' and 'location', or an un-headed format with Location in column 2 and Company Name in column 3.")


# -------------------------
# SINGLE ROW PROCESSING
# -------------------------
async def process_row(row: Dict, pipeline: OutreachPipeline, row_id: int) -> Dict:
    start_time = time.time()

    input_data = {
        "company_name": row.get("company_name"),
        "location": row.get("location")
    }

    # Basic validation
    if not input_data["company_name"]:
        return {
            "row_id": row_id,
            "input": input_data,
            "status": "failed",
            "error": "Missing company_name"
        }

    state = PipelineState(input=input_data)

    try:
        result = await pipeline.run(state)

        latency = round((time.time() - start_time) * 1000, 2)

        logger.info(f"[Row {row_id}] Success | {latency} ms")

        return {
            "row_id": row_id,
            "input": input_data,
            "business_profile": result.research.dict() if result.research else None,
            "contact": result.contact.dict() if result.contact else None,
            "outreach": result.outreach.dict() if result.outreach else None,
            "status": "success",
            "latency_ms": latency,
            "errors": result.errors
        }

    except Exception as e:
        latency = round((time.time() - start_time) * 1000, 2)

        logger.error(f"[Row {row_id}] Failed | {latency} ms | Error: {str(e)}")

        return {
            "row_id": row_id,
            "input": input_data,
            "status": "failed",
            "latency_ms": latency,
            "error": str(e)
        }


# -------------------------
# BATCH PROCESSING
# -------------------------
async def process_excel(file_path: str) -> List[Dict]:

    start_time = time.time()

    logger.info("Starting Excel processing")

    # Load file
    df = pd.read_excel(file_path)

    # Clean up fully empty rows (trailing rows in Excel)
    df.dropna(how="all", inplace=True)

    validate_columns(df)

    # Drop rows that don't even have a company_name
    df.dropna(subset=["company_name"], inplace=True)

    # Fill remaining NaNs with empty strings to prevent json/pydantic issues
    df = df.astype(object).fillna("")

    records = df.to_dict(orient="records")

    total_rows = len(records)

    logger.info(f"Total rows to process: {total_rows}")

    pipeline = OutreachPipeline()

    # -------------------------
    # CONCURRENCY CONTROL
    # -------------------------
    semaphore = asyncio.Semaphore(settings.SCRAPER_CONCURRENCY)

    async def safe_process(row, row_id):
        async with semaphore:
            logger.debug(f"[Row {row_id}] Processing started")
            return await process_row(row, pipeline, row_id)

    # -------------------------
    # TASK CREATION
    # -------------------------
    tasks = [
        safe_process(row, idx)
        for idx, row in enumerate(records, start=1)
    ]

    # -------------------------
    # PARALLEL EXECUTION
    # -------------------------
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # -------------------------
    # RESULT CLEANING
    # -------------------------
    final_results = []
    success_count = 0
    failure_count = 0

    for r in results:
        if isinstance(r, dict):
            final_results.append(r)

            if r.get("status") == "success":
                success_count += 1
            else:
                failure_count += 1

        else:
            failure_count += 1

            final_results.append({
                "status": "failed",
                "error": str(r)
            })

    total_time = round((time.time() - start_time), 2)

    # -------------------------
    # FINAL LOGGING
    # -------------------------
    logger.info(
        f"Processing complete | "
        f"Total: {total_rows}, "
        f"Success: {success_count}, "
        f"Failed: {failure_count}, "
        f"Time: {total_time}s"
    )

    return final_results