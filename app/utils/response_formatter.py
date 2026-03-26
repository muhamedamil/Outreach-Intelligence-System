# app/utils/response_formatter.py

from typing import List, Dict


def format_final_response(results: List[Dict], total_time: float) -> Dict:
    """
    Formats batch results into API response structure.
    """

    total = len(results)
    success = sum(1 for r in results if r.get("status") == "success")
    failed = total - success

    return {
        "summary": {
            "total_rows": total,
            "success": success,
            "failed": failed,
            "processing_time_sec": total_time
        },
        "results": results
    }