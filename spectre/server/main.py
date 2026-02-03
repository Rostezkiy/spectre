"""FastAPI application for Spectre."""

import logging
from typing import List

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from spectre.config import get_config
from spectre.core.models import Resource
from spectre.server.routes import router as api_router

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="Spectre API",
        description="Local-first REST API generated from captured network traffic",
        version="0.1.0",
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # In production, restrict appropriately
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Include API routes
    app.include_router(api_router, prefix="/api")

    # Health check endpoint
    @app.get("/health")
    async def health():
        return {"status": "healthy", "service": "spectre"}

    # Root endpoint
    @app.get("/")
    async def root():
        config = get_config()
        return {
            "message": "Spectre API",
            "project": config.project,
            "resources": [r.name for r in config.resources],
        }

    # Error handler for database connectivity
    @app.exception_handler(Exception)
    async def generic_exception_handler(request, exc):
        logger.exception("Unhandled exception")
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"},
        )

    return app


# Global app instance
app = create_app()


def get_configured_resources() -> List[Resource]:
    """Return the list of resources from the current configuration."""
    return get_config().resources