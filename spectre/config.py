"""Configuration loading for Spectre."""

import logging
import os
from pathlib import Path
from typing import Optional

import yaml
from dotenv import load_dotenv

from spectre.core.models import Resource, SpectreConfig

logger = logging.getLogger(__name__)


DEFAULT_CONFIG_NAME = "spectre.yaml"

CONFIG_ENV_VAR = "SPECTRE_CONFIG_PATH"

DB_ENV_VAR = "SPECTRE_DB_PATH"


def load_environment() -> None:
    """Load environment variables from .env file if present."""
    env_path = Path.cwd() / ".env"
    if env_path.exists():
        load_dotenv(env_path)
        logger.debug(f"Loaded environment from {env_path}")
    else:
        logger.debug("No .env file found, relying on system environment")


class SpectreConfigManager:
    """Singleton manager for Spectre configuration."""

    _instance: Optional["SpectreConfigManager"] = None
    _config: Optional[SpectreConfig] = None

    def __new__(cls) -> "SpectreConfigManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if not hasattr(self, "_initialized"):
            self._initialized = True
            load_environment()

    def _locate_config_file(self) -> Path:
        """Find the configuration file using environment variable or default."""
        env_path = os.getenv(CONFIG_ENV_VAR)
        if env_path:
            candidate = Path(env_path).expanduser()
            if candidate.exists():
                return candidate
            logger.warning(
                f"Config file specified in {CONFIG_ENV_VAR} does not exist: {env_path}"
            )

        default = Path.cwd() / DEFAULT_CONFIG_NAME
        if default.exists():
            return default

        logger.warning(
            f"No configuration file found. Using defaults. "
            f"Create {DEFAULT_CONFIG_NAME} or set {CONFIG_ENV_VAR}."
        )
        return default

    def _load_yaml(self, config_path: Path) -> dict:
        """Load YAML content from file."""
        if not config_path.exists():
            return {}

        encodings = ["utf-8", "utf-16", "cp1251"]

        for enc in encodings:
            try:
                with open(config_path, "r", encoding=enc) as f:
                    try:
                        data = yaml.safe_load(f)
                        logger.debug(f"Loaded YAML from {config_path} using {enc}")
                        return data or {}
                    except yaml.YAMLError as e:
                        logger.error(f"Failed to parse YAML {config_path}: {e}")
                        return {}
            except UnicodeDecodeError:
                continue
            except Exception as e:
                logger.error(f"Error reading config {config_path}: {e}")
                return {}

        logger.error(f"Could not read {config_path} with any supported encoding.")
        return {}

    def _build_config(self, yaml_data: dict) -> SpectreConfig:
        """Build a SpectreConfig instance from raw data."""

        db_path = os.getenv(DB_ENV_VAR)
        if not db_path:
            db_path = yaml_data.get("database_path", "./data/spectre.duckdb")

        raw_resources = yaml_data.get("resources", [])
        resources = []
        for r in raw_resources:
            try:
                resources.append(Resource(**r))
            except Exception as e:
                logger.warning(f"Invalid resource definition {r}: {e}")

        return SpectreConfig(
            project=yaml_data.get("project", "default"),
            base_url=yaml_data.get("base_url"),
            resources=resources,
            database_path=db_path,
        )

    def load(self, force_reload: bool = False) -> SpectreConfig:
        """
        Load configuration from file and environment.

        Args:
            force_reload: If True, reload even if already loaded.

        Returns:
            Loaded SpectreConfig instance.
        """
        if self._config is not None and not force_reload:
            return self._config

        config_path = self._locate_config_file()
        yaml_data = self._load_yaml(config_path)
        self._config = self._build_config(yaml_data)
        logger.info(f"Configuration loaded for project '{self._config.project}'")
        return self._config

    def get(self) -> SpectreConfig:
        """Get current config, loading if necessary."""
        if self._config is None:
            return self.load()
        return self._config

    def reset(self) -> None:
        """Reset the cached configuration (force reload on next get)."""
        self._config = None


_config_manager = SpectreConfigManager()


def get_config() -> SpectreConfig:
    """Shortcut to retrieve the global configuration."""
    return _config_manager.get()


def reload_config() -> SpectreConfig:
    """Force reload configuration from disk."""
    return _config_manager.load(force_reload=True)
