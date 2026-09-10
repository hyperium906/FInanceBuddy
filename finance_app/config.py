"""Application configuration.

Loads settings from environment variables (via a local ``.env`` file in
development) and fails fast at import time when anything required is missing.
No secrets or model names are hardcoded here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()

REQUIRED_VARS = (
    "GOOGLE_SHEET_ID",
    "GOOGLE_CREDS_PATH",
    "GEMINI_API_KEY",
    "GEMINI_MODEL",
    "GEMINI_MODEL_FAST",
)


class ConfigError(RuntimeError):
    """Raised at startup when required configuration is missing."""


@dataclass(frozen=True)
class Config:
    """Resolved application settings."""

    google_sheet_id: str
    google_creds_path: str
    gemini_api_key: str
    gemini_model: str
    gemini_model_fast: str


def _require(name: str) -> str:
    """Return the env var ``name``, or raise naming exactly what is missing."""
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigError(
            f"Missing required environment variable: {name}. "
            "Copy .env.example to .env and fill it in."
        )
    return value


def load_config() -> Config:
    """Build a :class:`Config`, reporting every missing variable at once."""
    missing = [name for name in REQUIRED_VARS if not os.getenv(name, "").strip()]
    if missing:
        raise ConfigError(
            "Missing required environment variable(s): "
            + ", ".join(missing)
            + ". Copy .env.example to .env and fill it in."
        )
    return Config(
        google_sheet_id=_require("GOOGLE_SHEET_ID"),
        google_creds_path=_require("GOOGLE_CREDS_PATH"),
        gemini_api_key=_require("GEMINI_API_KEY"),
        gemini_model=_require("GEMINI_MODEL"),
        gemini_model_fast=_require("GEMINI_MODEL_FAST"),
    )


@lru_cache(maxsize=1)
def get_config() -> Config:
    """Return the process-wide config, loading and validating it once."""
    return load_config()
