"""
DarkWeb Search API - Main application entry point.
RESTful API for searching .onion sites via Tor network.
"""
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from config import settings
from routes import health, search
from routes import jobs
from routes import webhooks
from core.tor_client import tor_client
from core.job_manager import job_manager
from core.webhook_manager import webhook_manager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s - %(name)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager.
    Handles startup and shutdown events.
    """
    # Startup
    logger.info("Starting DarkWeb Search API...")
    logger.info(f"Tor proxy: {settings.tor_proxy}")
    
    # Initialize Tor session
    tor_client.create_session()
    logger.info("Tor client initialized")
    
    # Start job manager (thread pool + recover interrupted jobs)
    job_manager.startup()
    logger.info("Job manager initialized")

    webhook_manager.startup()
    logger.info("Webhook manager initialized")

    yield

    # Shutdown
    logger.info("Shutting down DarkWeb Search API...")
    job_manager.shutdown()
    webhook_manager.shutdown()
    tor_client.close()
    logger.info("Cleanup complete")


# Create FastAPI application
app = FastAPI(
    title=settings.api_title,
    version=settings.api_version,
    description=settings.api_description,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc"
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(health.router)
app.include_router(search.router)
app.include_router(jobs.router)
app.include_router(webhooks.router)


@app.get("/", tags=["root"])
async def root():
    """
    Root endpoint with API information.
    """
    return {
        "name": settings.api_title,
        "version": settings.api_version,
        "description": settings.api_description,
        "docs": "/docs",
        "health": "/api/v1/health",
        "endpoints": {
            "search": "POST /api/v1/search",
            "jobs": "GET /api/v1/jobs",
            "job_detail": "GET /api/v1/jobs/{job_id}",
            "health": "GET /api/v1/health"
        }
    }


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """
    Global exception handler for unhandled errors.
    """
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error occurred"}
    )


if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info"
    )