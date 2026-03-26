# app/api/schema.py

from pydantic import BaseModel
from typing import List, Optional, Dict, Any


# INPUT SCHEMA 
class SingleInput(BaseModel):
    company_name: str
    location: Optional[str] = None


# ROW RESULT
class RowResult(BaseModel):
    row_id: int
    input: Dict[str, Any]
    business_profile: Optional[Dict[str, Any]]
    contact: Optional[Dict[str, Any]]
    outreach: Optional[Dict[str, Any]]
    status: str
    latency_ms: Optional[float]
    error: Optional[str] = None


# SUMMARY
class Summary(BaseModel):
    total_rows: int
    success: int
    failed: int
    processing_time_sec: float


# FINAL RESPONSE
class BatchResponse(BaseModel):
    summary: Summary
    results: List[RowResult]