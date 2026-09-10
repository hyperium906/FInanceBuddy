"""Optional voice input: record a clip in the browser, transcribe it locally.

Both halves are optional dependencies, and **their absence is a normal state,
not an error**. Import failures are caught at module load and reported through
:func:`availability`; the caller shows a typing-only UI and carries on. Nothing
in here raises for a missing package, a missing model, or a bad clip.

Transcription runs locally via faster-whisper, so no audio leaves the machine.
"""

from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

#: Whisper model size. "base" is a good speed/quality trade at ~141 MB on disk.
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base").strip() or "base"

#: int8 on CPU keeps it fast enough for a short clip on a laptop.
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "int8").strip() or "int8"

#: Clips shorter than this are almost certainly a misclick.
MIN_CLIP_BYTES = 2_000


# -- optional imports, resolved once at import time -------------------------

try:
    from audio_recorder_streamlit import audio_recorder as _audio_recorder
    RECORDER_IMPORT_ERROR: str | None = None
except Exception as exc:  # noqa: BLE001 - any import problem is "unavailable"
    _audio_recorder = None
    RECORDER_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"

try:
    from faster_whisper import WhisperModel as _WhisperModel
    WHISPER_IMPORT_ERROR: str | None = None
except Exception as exc:  # noqa: BLE001
    _WhisperModel = None
    WHISPER_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"


@dataclass(frozen=True, slots=True)
class Availability:
    """Which halves of voice input are usable right now."""

    recorder: bool
    transcriber: bool

    @property
    def usable(self) -> bool:
        """True only when a clip can be both captured and transcribed."""
        return self.recorder and self.transcriber

    @property
    def message(self) -> str:
        """What to tell the user when voice input is not available."""
        missing = []
        if not self.recorder:
            missing.append("`audio-recorder-streamlit`")
        if not self.transcriber:
            missing.append("`faster-whisper`")
        if not missing:
            return ""
        return (
            f"Voice input needs {' and '.join(missing)}. "
            "Install it with `pip install -r finance_app/requirements.txt`, "
            "then restart the app. Typing works either way."
        )


def availability() -> Availability:
    """Report whether recording and transcription are both available."""
    return Availability(
        recorder=_audio_recorder is not None,
        transcriber=_WhisperModel is not None,
    )


@dataclass(frozen=True, slots=True)
class Transcription:
    """The result of transcribing a clip. Never an exception."""

    text: str = ""
    ok: bool = False
    reason: str = ""
    language: str = ""
    duration: float = 0.0


def record(key: str = "advisor_voice", **kwargs) -> bytes | None:
    """Draw the record button and return the captured WAV bytes, if any.

    Returns None when the component is unavailable or nothing was recorded.
    """
    if _audio_recorder is None:
        return None
    try:
        return _audio_recorder(
            text=kwargs.pop("text", "Click to record"),
            icon_size=kwargs.pop("icon_size", "2x"),
            pause_threshold=kwargs.pop("pause_threshold", 2.0),
            key=key,
            **kwargs,
        )
    except Exception as exc:  # noqa: BLE001 - a broken widget must not kill the page
        log.warning("Audio recorder failed: %s", exc)
        return None


def _load_model():
    """Load the Whisper model. Cached as a Streamlit resource when possible.

    The first call downloads the model (~141 MB for "base") into the Hugging
    Face cache; later calls reuse it.
    """
    return _WhisperModel(
        WHISPER_MODEL, device="cpu", compute_type=WHISPER_COMPUTE_TYPE
    )


try:  # pragma: no cover - only meaningful inside a Streamlit runtime
    import streamlit as st

    _load_model = st.cache_resource(show_spinner=False)(_load_model)
except Exception:  # noqa: BLE001
    pass


def transcribe(audio: bytes | None) -> Transcription:
    """Turn recorded audio into text, locally. Never raises.

    Check ``ok``; ``reason`` explains anything that went wrong, including the
    model still downloading on first use.
    """
    if _WhisperModel is None:
        return Transcription(reason="faster-whisper is not installed.")
    if not audio:
        return Transcription(reason="No audio was recorded.")
    if len(audio) < MIN_CLIP_BYTES:
        return Transcription(reason="That clip was too short to transcribe.")

    path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
            handle.write(audio)
            path = handle.name

        model = _load_model()
        segments, info = model.transcribe(path, beam_size=1)
        text = " ".join(segment.text for segment in segments).strip()
    except Exception as exc:  # noqa: BLE001 - transcription is best-effort
        log.warning("Transcription failed: %s", exc)
        return Transcription(
            reason=f"Could not transcribe that clip ({type(exc).__name__}). "
                   "Type it instead."
        )
    finally:
        if path:
            Path(path).unlink(missing_ok=True)

    if not text:
        return Transcription(reason="Nothing recognisable in that clip.")
    return Transcription(
        text=text, ok=True,
        language=getattr(info, "language", "") or "",
        duration=float(getattr(info, "duration", 0.0) or 0.0),
    )
