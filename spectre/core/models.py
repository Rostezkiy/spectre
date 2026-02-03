"""Pydantic models for Spectre."""

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, HttpUrl, field_validator


class Blob(BaseModel):
    """Content‑addressable JSON blob."""

    hash: str = Field(..., description="SHA256 hash of the body")
    body: Dict[str, Any] = Field(..., description="JSON body as dictionary")
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        from_attributes = True


class Resource(BaseModel):
    """Definition of a REST resource derived from captured URLs."""

    name: str = Field(..., description="Resource name (e.g., 'products')")
    url_pattern: str = Field(..., description="Regex pattern for matching URLs")
    method: str = Field("GET", description="HTTP method to match")
    primary_key: Optional[str] = Field(
        None, description="Field inside JSON that acts as primary key"
    )

    @field_validator("method")
    @classmethod
    def method_uppercase(cls, v: str) -> str:
        return v.upper()


class CaptureBase(BaseModel):
    """Common fields for capture records."""

    session_id: str = Field(..., description="Identifier for the capture session")
    url: str = Field(..., description="Full request URL")
    method: str = Field("GET", description="HTTP method")
    headers: Optional[Dict[str, Any]] = Field(None, description="Response headers")
    status: int = Field(..., ge=100, le=599, description="HTTP status code")
    blob_hash: str = Field(..., description="SHA256 hash of the JSON body")

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        return v

    @field_validator("method")
    @classmethod
    def method_uppercase(cls, v: str) -> str:
        return v.upper()


class CaptureCreate(CaptureBase):
    """Model for inserting a new capture (without id and timestamp)."""

    pass


class Capture(CaptureBase):
    """Full capture record as stored in the database."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        from_attributes = True


class CaptureRequest(BaseModel):
    """
    Request model for external API insertion.

    Differs from CaptureCreate by accepting the raw JSON body
    instead of a pre‑computed blob hash.
    """

    session_id: str
    url: str
    method: str = "GET"
    headers: Optional[Dict[str, Any]] = None
    status: int
    body: Dict[str, Any] = Field(..., description="JSON response body")

    @field_validator("body")
    @classmethod
    def body_must_be_dict(cls, v: Any) -> Dict[str, Any]:
        if not isinstance(v, dict):
            raise ValueError("body must be a JSON object")
        return v


class SpectreConfig(BaseModel):
    """Root configuration model."""

    project: str = Field("default", description="Project name")
    base_url: Optional[HttpUrl] = Field(None, description="Base URL for captured API")
    resources: List[Resource] = Field(
        default_factory=list, description="List of resource definitions"
    )
    database_path: str = Field(
        "./data/spectre.duckdb", description="Path to DuckDB file"
    )
