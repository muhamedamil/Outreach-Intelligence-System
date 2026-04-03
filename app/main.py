import sys
import asyncio

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


# SERVE FRONTEND
app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")