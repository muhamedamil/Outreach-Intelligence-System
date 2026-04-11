import sys
import asyncio
import os

# ─────────────────────────────────────────────
# PRO-GRADE ENTRY POINT
# ─────────────────────────────────────────────
# WHY THIS FILE EXISTS:
# Uvicorn's --reload flag forces SelectorEventLoop on Windows,
# which breaks Playwright's subprocess spawning. There is NO way
# to override this from within the app itself.
#
# SOLUTION: We run without --reload. For dev auto-restart,
# use: pip install watchfiles && watchfiles "python run.py"
# ─────────────────────────────────────────────

if __name__ == "__main__":
    # Step 1: Force ProactorEventLoop BEFORE anything else
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        print("Windows ProactorEventLoop enforced.")
    
    import uvicorn
    
    print("Starting Lead Intelligence Engine on http://127.0.0.1:8010")
    print("   Press CTRL+C to stop.")
    print("")
    
    # Step 2: Run WITHOUT --reload to preserve the ProactorEventLoop
    # The --reload flag forces SelectorEventLoop on Windows, which
    # makes Playwright crash with NotImplementedError.
    #
    # workers=4: Allows the dashboard UI to remain accessible while
    # a long-running Apify/Playwright campaign is processing in another
    # worker. Without multiple workers, a single blocking campaign would
    # lock the entire server and make the page appear unreachable.
    uvicorn.run(
        "app.main:app", 
        host="127.0.0.1", 
        port=8010,
        reload=False,       # CRITICAL: --reload breaks Playwright on Windows
        workers=1,          # Single worker is required for stability on Windows
        log_level="info"
    )
