"""
DarkWeb Search API - Main application entry point.
RESTful API for searching .onion sites via Tor network.
"""
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from config import settings
from routes import health, search, jobs, capture, webhooks, screenshots
from routes import client as client_routes
from core.tor_client import tor_client
from core.job_manager import job_manager
from core.webhook_manager import webhook_manager
from core.client_keys import client_key_store

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

    client_key_store.startup()
    logger.info("Client key store initialized")

    yield

    # Shutdown
    logger.info("Shutting down DarkWeb Search API...")
    job_manager.shutdown()
    webhook_manager.shutdown()
    tor_client.close()
    logger.info("Cleanup complete")


# OpenAPI tags metadata — visible in /docs and /openapi.json
# These tags document the public nature of the API for consumers and code reviews.
openapi_tags = [
    {
        "name": "public-api",
        "description": (
            "This API is **intentionally public**. "
            "All endpoints expose publicly-available .onion search data. "
            "CORS is set to `*` and that is deliberate — any website can make "
            "XHR/fetch requests. No authentication is required for search, "
            "capture, or screenshot endpoints. See `config.live.yaml` (cors.origins) "
            "for the security rationale."
        ),
    },
    {
        "name": "root",
        "description": "API root and discovery endpoints.",
    },
]

# Create FastAPI application
app = FastAPI(
    title=settings.api_title,
    version=settings.api_version,
    description=settings.api_description,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_tags=openapi_tags,
    contact={
        "name": "Ognon Radar Project",
        "url": "https://github.com/ognon-radar",
        "email": "security@ognon-radar.local",
    },
    license_info={
        "name": "MIT License",
        "identifier": "MIT",
    },
)

# Configure CORS
# NOTE: allow_origins=["*"] is intentional — see openapi_tags "public-api".
# This is a public API. Restricting CORS would break frontends served from
# arbitrary domains (e.g. clients/www/ opened as file:// or hosted anywhere).
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

if settings.onion_location:
    @app.middleware("http")
    async def add_onion_location(request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers["Onion-Location"] = settings.onion_location
        return response

# Include routers
app.include_router(health.router)
app.include_router(search.router)
app.include_router(jobs.router)
app.include_router(capture.router)
app.include_router(screenshots.router)
app.include_router(webhooks.router)
app.include_router(client_routes.router)


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
            "health": "GET /api/v1/health",
            "capture": "POST /api/v1/capture",
            "capture_download": "GET /api/v1/captures/{job_id}/download",
            "screenshot": "POST /api/v1/screenshots",
            "screenshot_download": "GET /api/v1/screenshots/{storage_key}/download"
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