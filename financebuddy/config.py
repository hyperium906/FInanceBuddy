"""Application configuration.

Loads settings from environment variables (via a local ``.env`` file in
development, or the host's secret store in deployment) and fails fast at
import time when anything required is missing. No secrets or model names are
hardcoded here.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from dotenv import load_dotenv

load_dotenv()

#: Settings that must be present however the app is hosted. Credentials are
#: deliberately absent: they arrive as either a path or inline JSON, so they
#: are validated separately by :func:`_load_credentials`.
REQUIRED_VARS = (
    "GOOGLE_SHEET_ID",
    "GEMINI_API_KEY",
    "GEMINI_MODEL",
    "GEMINI_MODEL_FAST",
)


class ConfigError(RuntimeError):
    """Raised at startup when required configuration is missing."""


def _from_secrets(name: str) -> str:
    """Return ``name`` from Streamlit's secret store, or ``""``.

    Streamlit Community Cloud exposes top-level ``secrets.toml`` keys as
    environment variables too, so this is a fallback rather than the primary
    path — but it means a value pasted under a section still resolves, and it
    keeps the app working when only ``st.secrets`` is populated.

    Every failure is swallowed: outside Streamlit, or with no secrets file,
    touching ``st.secrets`` raises, and that is not an error here.
    """
    try:
        import streamlit as st

        return str(st.secrets[name]).strip()
    except Exception:  # noqa: BLE001 - absence is the normal case
        return ""


def _setting(name: str) -> str:
    """Return the configured value for ``name``, or ``""`` if unset."""
    return os.getenv(name, "").strip() or _from_secrets(name)


@dataclass(frozen=True)
class Config:
    """Resolved application settings.

    Exactly one of ``google_creds_path`` and ``google_creds_info`` is set.
    A deployed host has no file to point at, so it supplies the key inline;
    a development machine points at the downloaded JSON.
    """

    google_sheet_id: str
    gemini_api_key: str
    gemini_model: str
    gemini_model_fast: str
    google_creds_path: str = ""
    google_creds_info: dict[str, Any] | None = None

    @property
    def creds_source(self) -> str:
        """Where the service-account key came from, for error messages."""
        if self.google_creds_info is not None:
            email = self.google_creds_info.get("client_email", "unknown account")
            return f"GOOGLE_CREDS_JSON ({email})"
        return f"GOOGLE_CREDS_PATH ({self.google_creds_path})"


def _decode_creds_json(raw: str) -> dict[str, Any]:
    """Parse an inline service-account key, accepting raw JSON or base64.

    Hosts differ in how happily they carry a multi-line value, so a key that
    has been base64-encoded to survive the trip is accepted as well.
    """
    try:
        info = json.loads(raw)
    except json.JSONDecodeError as exc:
        # Not JSON as given. It may have been base64-encoded to survive a host
        # that will not carry a multi-line value, so try that before giving up.
        try:
            info = json.loads(base64.b64decode(raw, validate=True).decode("utf-8"))
        except (binascii.Error, UnicodeDecodeError, ValueError):
            if raw.lstrip().startswith("{"):
                # It was meant to be JSON, so report where it went wrong rather
                # than blaming the encoding.
                raise ConfigError(
                    f"GOOGLE_CREDS_JSON is not valid JSON: {exc.msg} "
                    f"(line {exc.lineno}). Paste the service-account key file "
                    "exactly as downloaded."
                ) from exc
            raise ConfigError(
                "GOOGLE_CREDS_JSON is neither JSON nor base64-encoded JSON. "
                "Paste the whole service-account key file, braces included — "
                "not the path to it."
            ) from exc

    if not isinstance(info, dict):
        raise ConfigError("GOOGLE_CREDS_JSON must be a JSON object, not a list or scalar.")

    missing = [k for k in ("client_email", "private_key") if not info.get(k)]
    if missing:
        raise ConfigError(
            "GOOGLE_CREDS_JSON is missing " + ", ".join(missing) + ". "
            "That is not a service-account key — download one from "
            "Google Cloud → Credentials → Service account → Keys → Add key."
        )
    return info


def _load_credentials() -> tuple[str, dict[str, Any] | None]:
    """Resolve the service-account key to ``(path, info)``, one of them empty."""
    inline = _setting("GOOGLE_CREDS_JSON")
    path = _setting("GOOGLE_CREDS_PATH")

    if inline:
        return "", _decode_creds_json(inline)
    if path:
        return path, None
    raise ConfigError(
        "No Google service-account key configured. Set GOOGLE_CREDS_JSON to the "
        "contents of the key file (the usual choice when deployed, since the "
        "file is gitignored and will not exist on the server), or "
        "GOOGLE_CREDS_PATH to its location on disk."
    )


def load_config() -> Config:
    """Build a :class:`Config`, reporting every missing variable at once."""
    missing = [name for name in REQUIRED_VARS if not _setting(name)]
    if missing:
        raise ConfigError(
            "Missing required configuration: "
            + ", ".join(missing)
            + ". Copy .env.example to .env and fill it in, or set these in your "
            "host's secrets."
        )

    creds_path, creds_info = _load_credentials()
    return Config(
        google_sheet_id=_setting("GOOGLE_SHEET_ID"),
        gemini_api_key=_setting("GEMINI_API_KEY"),
        gemini_model=_setting("GEMINI_MODEL"),
        gemini_model_fast=_setting("GEMINI_MODEL_FAST"),
        google_creds_path=creds_path,
        google_creds_info=creds_info,
    )


@lru_cache(maxsize=1)
def get_config() -> Config:
    """Return the process-wide config, loading and validating it once."""
    return load_config()
