"""Dynamic API routes for Spectre."""

import logging
import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from fastapi.responses import JSONResponse

from spectre.config import get_config
from spectre.core.models import Resource
from spectre.database import DatabaseConnection

logger = logging.getLogger(__name__)

router = APIRouter()


def get_resource_by_name(name: str) -> Resource:
    """Look up a resource by its name."""
    config = get_config()
    for resource in config.resources:
        if resource.name == name:
            return resource
    raise HTTPException(
        status_code=404, detail=f"Resource '{name}' not found"
    )


def build_filter_conditions(
    filters: Dict[str, Any],
) -> tuple[str, List[Any]]:
    """
    Convert query parameters into SQL WHERE conditions for JSON fields.

    Supports simple equality: `?field=value` → `body->>'field' = ?`
    Supports special operators:
        field__gt, field__lt, field__gte, field__lte,
        field__contains, field__startswith, field__endswith

    Returns:
        SQL condition string and list of parameter values.
    """
    conditions = []
    values = []

    for key, value in filters.items():
        if "__" in key:
            field, operator = key.rsplit("__", 1)
            column = f"body->>'{field}'"
            if operator == "gt":
                conditions.append(f"{column} > ?")
            elif operator == "lt":
                conditions.append(f"{column} < ?")
            elif operator == "gte":
                conditions.append(f"{column} >= ?")
            elif operator == "lte":
                conditions.append(f"{column} <= ?")
            elif operator == "contains":
                conditions.append(f"{column} LIKE ?")
                values.append(f"%{value}%")
                continue
            elif operator == "startswith":
                conditions.append(f"{column} LIKE ?")
                values.append(f"{value}%")
                continue
            elif operator == "endswith":
                conditions.append(f"{column} LIKE ?")
                values.append(f"%{value}")
                continue
            elif operator == "neq":
                conditions.append(f"{column} != ?")
            else:
                # Unknown operator, treat as equality on the whole key
                column = f"body->>'{key}'"
                conditions.append(f"{column} = ?")
        else:
            column = f"body->>'{key}'"
            conditions.append(f"{column} = ?")
        values.append(value)

    if conditions:
        return " AND ".join(conditions), values
    return "1=1", []  # no filter


@router.get("/{resource_name}")
async def list_resource(
    resource_name: str = Path(..., description="Resource name"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    # Dynamic filter parameters: any key=value pair
    filters: Dict[str, Any] = Depends(lambda: {}),
    # Optional sorting
    sort: Optional[str] = Query(None, description="Field to sort by"),
    order: str = Query("asc", regex="^(asc|desc)$"),
):
    """
    List captured records for a given resource.

    Supports filtering by JSON fields via query parameters.
    Example: `/api/products?category=electronics&price__gt=100`
    """
    resource = get_resource_by_name(resource_name)

    # Build WHERE clause for URL pattern
    url_pattern = resource.url_pattern
    # If pattern contains {int}, {uuid}, {id} placeholders, convert to regex?
    # For simplicity, we treat url_pattern as a SQL LIKE pattern.
    # However, the analyzer generates regex patterns; we need to convert.
    # For MVP, assume url_pattern is already a SQL LIKE pattern.
    where_clauses = ["c.url LIKE ?"]
    params = [url_pattern]

    # Add JSON field filters
    filter_sql, filter_params = build_filter_conditions(filters)
    where_clauses.append(filter_sql)
    params.extend(filter_params)

    where_sql = " AND ".join(f"({wc})" for wc in where_clauses)

    # Sorting
    order_by = ""
    if sort:
        # Validate sort field exists? Not strictly necessary.
        order_by = f"ORDER BY body->>'{sort}' {order.upper()}"

    # Query
    sql = f"""
        SELECT c.id, c.url, c.method, c.status, c.timestamp, b.body
        FROM captures c
        JOIN blobs b ON c.blob_hash = b.hash
        WHERE {where_sql}
        {order_by}
        LIMIT ? OFFSET ?
    """
    params.extend([limit, offset])

    try:
        with DatabaseConnection() as conn:
            rows = conn.execute(sql, params).fetchall()
    except Exception as e:
        logger.exception("Database query failed")
        raise HTTPException(status_code=500, detail="Internal database error")

    # Convert to list of JSON objects
    records = []
    for row in rows:
        record = {
            "id": row[0],
            "url": row[1],
            "method": row[2],
            "status": row[3],
            "timestamp": row[4],
            **row[5],  # body is already a dict
        }
        records.append(record)

    # Total count for pagination metadata
    count_sql = f"""
        SELECT COUNT(*)
        FROM captures c
        JOIN blobs b ON c.blob_hash = b.hash
        WHERE {where_sql}
    """
    count_params = params[:-2]  # exclude limit/offset
    with DatabaseConnection() as conn:
        total = conn.execute(count_sql, count_params).fetchone()[0]

    return {
        "resource": resource_name,
        "total": total,
        "limit": limit,
        "offset": offset,
        "data": records,
    }


@router.get("/{resource_name}/{record_id}")
async def get_resource_record(
    resource_name: str = Path(..., description="Resource name"),
    record_id: str = Path(..., description="Record ID (UUID)"),
):
    """Get a single captured record by its ID."""
    resource = get_resource_by_name(resource_name)

    sql = """
        SELECT c.id, c.url, c.method, c.status, c.timestamp, b.body
        FROM captures c
        JOIN blobs b ON c.blob_hash = b.hash
        WHERE c.url LIKE ? AND c.id = ?
    """
    params = [resource.url_pattern, record_id]

    try:
        with DatabaseConnection() as conn:
            row = conn.execute(sql, params).fetchone()
    except Exception as e:
        logger.exception("Database query failed")
        raise HTTPException(status_code=500, detail="Internal database error")

    if not row:
        raise HTTPException(status_code=404, detail="Record not found")

    record = {
        "id": row[0],
        "url": row[1],
        "method": row[2],
        "status": row[3],
        "timestamp": row[4],
        **row[5],
    }
    return record


@router.get("/{resource_name}/latest")
async def get_latest_resource_record(
    resource_name: str = Path(..., description="Resource name"),
):
    """Get the most recent captured record for a resource."""
    resource = get_resource_by_name(resource_name)

    sql = """
        SELECT c.id, c.url, c.method, c.status, c.timestamp, b.body
        FROM captures c
        JOIN blobs b ON c.blob_hash = b.hash
        WHERE c.url LIKE ?
        ORDER BY c.timestamp DESC
        LIMIT 1
    """
    params = [resource.url_pattern]

    try:
        with DatabaseConnection() as conn:
            row = conn.execute(sql, params).fetchone()
    except Exception as e:
        logger.exception("Database query failed")
        raise HTTPException(status_code=500, detail="Internal database error")

    if not row:
        raise HTTPException(status_code=404, detail="No captures found")

    record = {
        "id": row[0],
        "url": row[1],
        "method": row[2],
        "status": row[3],
        "timestamp": row[4],
        **row[5],
    }
    return record


@router.get("/{resource_name}/history")
async def get_resource_history(
    resource_name: str = Path(..., description="Resource name"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Get capture history for a resource (timeline of captures)."""
    resource = get_resource_by_name(resource_name)

    sql = """
        SELECT c.id, c.url, c.method, c.status, c.timestamp
        FROM captures c
        WHERE c.url LIKE ?
        ORDER BY c.timestamp DESC
        LIMIT ? OFFSET ?
    """
    params = [resource.url_pattern, limit, offset]

    try:
        with DatabaseConnection() as conn:
            rows = conn.execute(sql, params).fetchall()
    except Exception as e:
        logger.exception("Database query failed")
        raise HTTPException(status_code=500, detail="Internal database error")

    history = [
        {
            "id": row[0],
            "url": row[1],
            "method": row[2],
            "status": row[3],
            "timestamp": row[4],
        }
        for row in rows
    ]

    # Total count
    count_sql = "SELECT COUNT(*) FROM captures WHERE url LIKE ?"
    with DatabaseConnection() as conn:
        total = conn.execute(count_sql, [resource.url_pattern]).fetchone()[0]

    return {
        "resource": resource_name,
        "total": total,
        "limit": limit,
        "offset": offset,
        "history": history,
    }