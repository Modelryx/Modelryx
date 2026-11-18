from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MODELRYX_", env_file=".env", extra="ignore")

    app_name: str = "Modelryx API"
    environment: Literal["development", "test", "production"] = "development"
    database_url: str = "postgresql://modelryx:modelryx@localhost:5432/modelryx"
    database_pool_size: int = 20
    database_max_overflow: int = 40
    database_pool_timeout: int = 30
    database_pool_recycle: int = 3600
    storage_root: Path = Path("data/objects")
    jwt_secret: str = "change-this-development-secret"
    jwt_issuer: str = "modelryx"
    jwt_expiry_minutes: int = 60
    bootstrap_admin_username: str = "admin"
    bootstrap_admin_password: str = "change-this-admin-password"
    bootstrap_admin_display_name: str = "Modelryx Administrator"
    cors_origins: list[str] = ["http://localhost:5173"]
    log_level: str = "INFO"
    max_upload_bytes: int = 2 * 1024 * 1024 * 1024
    max_assembly_files: int = 500
    max_assembly_occurrences: int = 10_000
    max_assembly_upload_bytes: int = 20 * 1024 * 1024 * 1024
    upload_expiry_seconds: int = 3600
    checkout_expiry_seconds: int = 8 * 60 * 60
    checkout_expiry_warning_seconds: int = 60 * 60
    orphan_grace_seconds: int = 86400
    converter_version: str = "modelryx-fcstd-29"
    converter_timeout_seconds: int = 1800
    server_url: str = "http://localhost:8000"
    cad_analyzer_url: str | None = None
    freecad_preprocessor_url: str | None = None
    step_worker_url: str = "http://localhost:8080"
    freecad_worker_url: str | None = None
    internal_worker_key: str | None = None
    max_preview_package_bytes: int = 2 * 1024 * 1024 * 1024
    max_preview_glb_bytes: int = 1024 * 1024 * 1024
    max_preview_bom_bytes: int = 64 * 1024 * 1024
    max_image_preview_bytes: int = 50 * 1024 * 1024
    max_pdf_preview_bytes: int = 50 * 1024 * 1024
    max_preview_nodes: int = 100_000
    max_preview_triangles: int = 5_000_000
    worker_poll_seconds: float = 2.0
    processing_worker_role: Literal["general"] = "general"
    worker_heartbeat_timeout_seconds: int = 30
    maintenance_cleanup_seconds: float = 300.0
    maintenance_cleanup_failure_threshold: int = 3
    processing_stale_seconds: int = 3600
    cad_session_expiry_seconds: int = 2 * 60 * 60
    cad_session_max_bytes: int = 2 * 1024 * 1024 * 1024
    ai_gateway_base_url: str | None = None
    ai_gateway_api_key: str | None = None
    ai_chat_model: str = ""
    ai_embedding_model: str = ""
    ai_embedding_dimensions: int = 1536
    ai_request_timeout_seconds: int = 60
    ai_retrieval_limit: int = 8
    ai_search_store_raw_queries: bool = False
    ai_search_history_retention_days: int = 30
    require_release_approval: bool = False
    approval_required_count: int = 1
    approval_assembly_required_count: int = 1
    approval_due_hours: int = 72
    approval_emergency_due_hours: int = 4
    duplicate_metadata_weight: float = 0.75
    duplicate_embedding_weight: float = 0.25
    duplicate_engineering_min: float = 0.985
    duplicate_likely_min: float = 0.90
    duplicate_similar_min: float = 0.72
    duplicate_ai_review_min: float = 0.88
    duplicate_ai_review_max: float = 0.985
    duplicate_max_ai_candidates: int = 5

    @model_validator(mode="after")
    def secure_production_authentication(self) -> "Settings":
        if self.ai_embedding_dimensions != 1536:
            raise ValueError("AI embedding dimensions must be 1536 for the current vector schema")
        if not 1 <= self.ai_search_history_retention_days <= 3650:
            raise ValueError("AI search history retention must be between 1 and 3650 days")
        if not 1 <= self.approval_required_count <= 5:
            raise ValueError("approval required count must be between 1 and 5")
        if not 1 <= self.approval_assembly_required_count <= 5:
            raise ValueError("assembly approval required count must be between 1 and 5")
        if not 1 <= self.approval_due_hours <= 8760:
            raise ValueError("approval due hours must be between 1 and 8760")
        if not 1 <= self.approval_emergency_due_hours <= 24:
            raise ValueError("emergency approval due hours must be between 1 and 24")
        duplicate_values = (
            self.duplicate_metadata_weight,
            self.duplicate_embedding_weight,
            self.duplicate_engineering_min,
            self.duplicate_likely_min,
            self.duplicate_similar_min,
            self.duplicate_ai_review_min,
            self.duplicate_ai_review_max,
        )
        if any(not 0 <= value <= 1 for value in duplicate_values):
            raise ValueError("duplicate detection weights and thresholds must be between 0 and 1")
        if abs(self.duplicate_metadata_weight + self.duplicate_embedding_weight - 1) > 1e-9:
            raise ValueError("duplicate metadata and embedding weights must total 1")
        if not (
            self.duplicate_engineering_min
            >= self.duplicate_likely_min
            >= self.duplicate_similar_min
        ):
            raise ValueError(
                "duplicate thresholds must be ordered engineering >= likely >= similar"
            )
        if self.duplicate_ai_review_min > self.duplicate_ai_review_max:
            raise ValueError("duplicate AI review minimum must not exceed its maximum")
        if not 0 <= self.duplicate_max_ai_candidates <= 20:
            raise ValueError("duplicate max AI candidates must be between 0 and 20")
        if self.environment != "production":
            return self

        def is_placeholder(value: str) -> bool:
            normalized = value.strip().lower()
            return (
                normalized in {"admin", "password", "changeme"}
                or normalized.startswith(
                    ("replace-", "change-this-", "ci-only-", "local-validation-")
                )
                or len(set(normalized)) < 8
            )

        if len(self.jwt_secret.strip()) < 32 or is_placeholder(self.jwt_secret):
            raise ValueError("production requires a unique JWT secret of at least 32 characters")
        if len(self.bootstrap_admin_password.strip()) < 16 or is_placeholder(
            self.bootstrap_admin_password
        ):
            raise ValueError(
                "production requires a unique administrator password of at least 16 characters"
            )
        if (
            not self.internal_worker_key
            or len(self.internal_worker_key.strip()) < 32
            or is_placeholder(self.internal_worker_key)
        ):
            raise ValueError("production requires a unique worker key of at least 32 characters")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
