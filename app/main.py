import sys
import asyncio
from pathlib import Path

# 🚀 PRO-GRADE WINDOWS HARDENING:
# ------------------------------------------------------------
# This MUST be the absolute first line of execution.
# Windows requires the ProactorEventLoopPolicy to handle 
# Playwright and other subprocess-based async tasks.
# ------------------------------------------------------------
if sys.platform == 'win32':
    try:
        from asyncio import WindowsProactorEventLoopPolicy
        asyncio.set_event_loop_policy(WindowsProactorEventLoopPolicy())
    except Exception:
        # Already set or unsupported environment
        pass

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.config.settings import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)


# APP INITIALIZATION
app = FastAPI(
    title="Multi-Agent Outreach System",
    description="AI-powered pipeline for business research, contact discovery, and outreach generation",
    version="1.0.0"
)


# MIDDLEWARE (CORS)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# NO-CACHE MIDDLEWARE — Forces browser to always fetch fresh HTML/CSS/JS
# This prevents stale UI from being served after code updates.
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

class NoCacheMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if request.url.path.endswith(('.html', '.css', '.js')) or request.url.path == '/':
            response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
            response.headers['Pragma'] = 'no-cache'
            response.headers['Expires'] = '0'
        return response

app.add_middleware(NoCacheMiddleware)


# ROUTES
app.include_router(router, prefix="/api")


# STARTUP EVENT
@app.on_event("startup")
async def startup_event():
    # Defensive re-check: Force the Proactor loop if it was somehow reset by a library
    if sys.platform == 'win32':
        try:
            loop = asyncio.get_event_loop_policy()
            if not isinstance(loop, WindowsProactorEventLoopPolicy):
                asyncio.set_event_loop_policy(WindowsProactorEventLoopPolicy())
        except Exception:
            pass
            
    logger.info("Application startup")


# SHUTDOWN EVENT
@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Application shutdown")


# SERVE FRONTEND — use absolute path so it works regardless of CWD
_FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
app.mount("/", StaticFiles(directory=str(_FRONTEND_DIR), html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn
    print("Direct execution - Starting uvicorn...")
    uvicorn.run(app, host="127.0.0.1", port=8000)