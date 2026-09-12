"""The only module that talks to an LLM provider.

Everything else in the app calls :func:`generate`. Swapping providers means
rewriting this file and nothing else, so keep provider types (``google.genai``
objects, provider-specific exceptions) from leaking out of it — callers see
``str``, parsed JSON, and :class:`LLMError`.

Model names always come from configuration; none are hardcoded here.
"""

from __future__ import annotations

import json
import logging
import time
from functools import lru_cache
from typing import Any, Callable

from google import genai
from google.genai import errors, types

from financebuddy.config import get_config

log = logging.getLogger(__name__)

#: Backoff delays (seconds) after each rate-limited attempt. Five attempts total.
RETRY_DELAYS: tuple[float, ...] = (1.0, 2.0, 4.0, 8.0)

#: HTTP statuses worth retrying: rate limit, plus transient server faults.
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


class LLMError(RuntimeError):
    """An LLM call failed, or returned something unusable."""


class RateLimited(LLMError):
    """Retries were exhausted while the provider kept returning 429."""


@lru_cache(maxsize=1)
def _client() -> genai.Client:
    """The provider client, built once per process from configuration."""
    return genai.Client(api_key=get_config().gemini_api_key)


def _status_of(exc: errors.APIError) -> int | None:
    """HTTP status behind a provider error, if it exposes one."""
    for attr in ("code", "status_code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    return None


def generate(
    prompt: str,
    schema: Any | None = None,
    model: str | None = None,
    *,
    on_retry: Callable[[int, float, Exception], None] | None = None,
    temperature: float = 0.0,
) -> Any:
    """Send ``prompt`` to the model and return its answer.

    With ``schema``, the provider is constrained to emit JSON matching it and
    the parsed object is returned. Without one, the raw text is returned.

    ``model`` defaults to ``GEMINI_MODEL``; pass ``GEMINI_MODEL_FAST`` for bulk
    work. Rate limits (429) and transient 5xx are retried with exponential
    backoff — 1s, 2s, 4s, 8s — and ``on_retry(attempt, delay, error)`` is called
    before each wait so the UI can say what is happening.

    Raises :class:`RateLimited` if every retry is rate-limited, or
    :class:`LLMError` for any other failure.
    """
    # Only touch config when a model is not supplied, so callers that pass one
    # explicitly do not need the whole environment resolved.
    target = model or get_config().gemini_model

    config = types.GenerateContentConfig(temperature=temperature)
    if schema is not None:
        config.response_mime_type = "application/json"
        config.response_schema = schema

    last: Exception | None = None
    for attempt in range(len(RETRY_DELAYS) + 1):
        try:
            response = _client().models.generate_content(
                model=target, contents=prompt, config=config
            )
            return _unpack(response, schema)
        except errors.APIError as exc:
            status = _status_of(exc)
            if status not in RETRY_STATUSES or attempt == len(RETRY_DELAYS):
                last = exc
                break
            delay = RETRY_DELAYS[attempt]
            log.warning(
                "LLM %s from %s; retrying in %.1fs (attempt %d/%d)",
                status, target, delay, attempt + 1, len(RETRY_DELAYS),
            )
            if on_retry is not None:
                on_retry(attempt + 1, delay, exc)
            time.sleep(delay)
            last = exc

    status = _status_of(last) if isinstance(last, errors.APIError) else None
    if status == 429:
        raise RateLimited(
            f"{target} is rate limited and did not recover after "
            f"{len(RETRY_DELAYS)} retries. The free tier allows roughly 10-15 "
            "requests per minute; wait a moment and try again."
        ) from last
    raise LLMError(f"Call to {target} failed: {last}") from last


def _unpack(response: types.GenerateContentResponse, schema: Any | None) -> Any:
    """Pull the answer out of a provider response, or raise :class:`LLMError`."""
    if schema is None:
        text = getattr(response, "text", None)
        if not text:
            raise LLMError("Model returned an empty response.")
        return text

    parsed = getattr(response, "parsed", None)
    if parsed is not None:
        return parsed

    # Structured output was requested but the SDK could not parse it; fall back
    # to decoding the raw text before giving up.
    text = (getattr(response, "text", None) or "").strip()
    if not text:
        raise LLMError("Model returned an empty response where JSON was required.")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMError(
            f"Model did not return valid JSON despite a schema: {text[:200]!r}"
        ) from exc
