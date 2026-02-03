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

def pattern_to_regex(pattern: str) -> str:
    """
    Превращает /api/users/{int} -> ^.*/api/users/\\d+$
    """
    regex = re.escape(pattern)
    
    regex = regex.replace(r"\{int\}", r"\d+")
    regex = regex.replace(r"\{uuid\}", r"[0-9a-fA-F-]{36}")
    regex = regex.replace(r"\{id\}", r"[^/]+")

    if regex.startswith("/"):
        regex = ".*" + regex
        
    return regex

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
        field_name_check = key.split("__")[0]
        if not re.match(r"^[a-zA-Z0-9_]+$", field_name_check):
            logger.warning(f"Ignored unsafe filter key: {key}")
            continue

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
    import json
    resource = get_resource_by_name(resource_name)

    # Build WHERE clause for URL pattern
    url_pattern = resource.url_pattern
    regex_pattern = pattern_to_regex(url_pattern)
    where_clauses = ["regexp_full_match(c.url, ?)"]
    params = [regex_pattern]

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
        body_data = row[5]
        
        # ЗАЩИТА ОТ DUCKDB: Если вернулась строка, парсим её в dict
        if isinstance(body_data, str):
            try:
                body_data = json.loads(body_data)
            except json.JSONDecodeError:
                body_data = {}  # Fallback на случай битых данных
        
        # Если body_data None (бывает при NULL в базе), заменяем на пустой dict
        if body_data is None:
            body_data = {}

        record = {
            "id": row[0],
            "url": row[1],
            "method": row[2],
            "status": row[3],
            "timestamp": row[4],
            **body_data,  # Теперь это гарантированно словарь
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
    regex_pattern = pattern_to_regex(resource.url_pattern)
    
    sql = """
        SELECT c.id, c.url, c.method, c.status, c.timestamp, b.body
        FROM captures c
        JOIN blobs b ON c.blob_hash = b.hash
        WHERE regexp_matches(c.url, ?) AND c.id = ?
    """
    params = [regex_pattern, record_id]

    try:
        with DatabaseConnection() as conn:
            row = conn.execute(sql, params).fetchone()
    except Exception as e:
        logger.exception("Database query failed")
        raise HTTPException(status_code=500, detail="Internal database error")

    if not row:
        raise HTTPException(status_code=404, detail="Record not found")

    import json
    body_data = row[5]
    if isinstance(body_data, str):
        try:
            body_data = json.loads(body_data)
        except:
            body_data = {}
    if body_data is None: 
        body_data = {}

    record = {
        "id": row[0],
        "url": row[1],
        "method": row[2],
        "status": row[3],
        "timestamp": row[4],
        **body_data,
    }
    return record

@router.get("/{resource_name}/latest")
async def get_latest_resource_record(
    resource_name: str = Path(..., description="Resource name"),
):
    """Get the most recent captured record for a resource."""
    resource = get_resource_by_name(resource_name)
    
    regex_pattern = pattern_to_regex(resource.url_pattern)

    sql = """
        SELECT c.id, c.url, c.method, c.status, c.timestamp, b.body
        FROM captures c
        JOIN blobs b ON c.blob_hash = b.hash
        WHERE regexp_matches(c.url, ?)
        ORDER BY c.timestamp DESC
        LIMIT 1
    """
    params = [regex_pattern]

    try:
        with DatabaseConnection() as conn:
            row = conn.execute(sql, params).fetchone()
    except Exception as e:
        logger.exception("Database query failed")
        raise HTTPException(status_code=500, detail="Internal database error")

    if not row:
        raise HTTPException(status_code=404, detail="No captures found")

    import json
    body_data = row[5]
    if isinstance(body_data, str):
        try:
            body_data = json.loads(body_data)
        except:
            body_data = {}
    if body_data is None: 
        body_data = {}

    record = {
        "id": row[0],
        "url": row[1],
        "method": row[2],
        "status": row[3],
        "timestamp": row[4],
        **body_data,
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
    
    regex_pattern = pattern_to_regex(resource.url_pattern)

    sql = """
        SELECT c.id, c.url, c.method, c.status, c.timestamp
        FROM captures c
        WHERE regexp_matches(c.url, ?)
        ORDER BY c.timestamp DESC
        LIMIT ? OFFSET ?
    """
    params = [regex_pattern, limit, offset]

    try:
        with DatabaseConnection() as conn:
            rows = conn.execute(sql, params).fetchall()
            
            count_sql = "SELECT COUNT(*) FROM captures WHERE regexp_matches(url, ?)"
            total = conn.execute(count_sql, [regex_pattern]).fetchone()[0]
            
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

    return {
        "resource": resource_name,
        "total": total,
        "limit": limit,
        "offset": offset,
        "history": history,
    }