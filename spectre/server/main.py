"""FastAPI application for Spectre."""

import logging
from typing import List, Callable

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from spectre.config import get_config
from spectre.core.models import Resource
from spectre.server.routes import router as api_router, list_resource, get_resource_record

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="Spectre API",
        description="Local-first REST API generated from captured network traffic",
        version="0.2.0",
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # In production, restrict appropriately
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router, prefix="/api")

    config = get_config()

    for res in config.resources:
        def create_resource_lister(resource_name: str) -> Callable:
            """Factory to create a specific endpoint handler for a resource."""
            async def specific_list_resource(
                limit: int = 100,
                offset: int = 0,
                sort: str = None,
                order: str = "asc",
            ):
                return await list_resource(
                    resource_name=resource_name,
                    limit=limit,
                    offset=offset,
                    sort=sort,
                    order=order,
                    filters={} # Note: Simple version, complex filters via dependency injection is harder dynamically
                )
            return specific_list_resource

        app.add_api_route(
            path=f"/api/{res.name}",
            endpoint=create_resource_lister(res.name),
            methods=[res.method],
            tags=[res.name],
            summary=f"List {res.name}",
            description=f"Auto-generated route for {res.url_pattern}"
        )
        
        # Register Detail Endpoint (GET /api/products/{id})
        # Note: We reuse the generic logic but bind the resource_name
        async def specific_get_record(record_id: str, res_name: str = res.name):
            return await get_resource_record(resource_name=res_name, record_id=record_id)
            
        app.add_api_route(
            path=f"/api/{res.name}/{{record_id}}",
            endpoint=specific_get_record,
            methods=["GET"],
            tags=[res.name],
            summary=f"Get {res.name} by ID"
        )

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