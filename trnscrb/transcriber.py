"""Local transcription via faster-whisper.

Model is loaded once and reused. On Apple Silicon the Metal GPU backend
is selected automatically via device="auto".
"""
import threading
from pathlib import Path

_model = None
_model_lock = threading.Lock()
_model_size = "small"


def set_model_size(size: str) -> None:
    global _model_size, _model
    _model_size = size
    _model = None  # force reload on next call


def _get_model():
    global _model
    with _model_lock:
        if _model is None:
            from faster_whisper import WhisperModel
            _model = WhisperModel(_model_size, device="auto", compute_type="auto")
        return _model


def transcribe(audio_path: Path) -> list[dict]:
    """Return segments: [{start, end, text, speaker}] — speaker filled later by diarizer."""
    model = _get_model()
    segments, _info = model.transcribe(
        str(audio_path),
        beam_size=5,
        vad_filter=True,   # skip silent gaps automatically
        language=None,     # auto-detect
    )
    return [
        {
            "start": seg.start,
            "end": seg.end,
            "text": seg.text.strip(),
            "speaker": None,
        }
        for seg in segments
        if seg.text.strip()
    ]
