# app/main.py

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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
    logger.info("Application startup")


# SHUTDOWN EVENT
@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Application shutdown")


# ROOT ENDPOINT
@app.get("/")
async def root():
    return {
        "message": "Multi-Agent Outreach System API",
        "status": "running",
        "docs": "/docs"
    }