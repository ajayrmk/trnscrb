"""Speaker diarization via pyannote.audio.

Requires a HuggingFace token with access to:
  pyannote/speaker-diarization-3.1
(accept the model's conditions at hf.co once, then it works offline).
"""
from pathlib import Path


def diarize(audio_path: Path, hf_token: str) -> list[dict]:
    """Return [{start, end, speaker}] segments."""
    from pyannote.audio import Pipeline
    import torch

    pipeline = Pipeline.from_pretrained(
        "pyannote/speaker-diarization-3.1",
        use_auth_token=hf_token,
    )

    # Prefer Apple Silicon Metal, fallback to CPU
    if torch.backends.mps.is_available():
        pipeline = pipeline.to(torch.device("mps"))

    diarization = pipeline(str(audio_path))

    return [
        {"start": turn.start, "end": turn.end, "speaker": speaker}
        for turn, _, speaker in diarization.itertracks(yield_label=True)
    ]


def merge(transcript: list[dict], diarization: list[dict]) -> list[dict]:
    """Assign the best-matching speaker label to each transcript segment."""
    for seg in transcript:
        best_speaker = None
        best_overlap = 0.0
        for d in diarization:
            overlap = min(seg["end"], d["end"]) - max(seg["start"], d["start"])
            if overlap > best_overlap:
                best_overlap = overlap
                best_speaker = d["speaker"]
        seg["speaker"] = best_speaker or "Unknown"
    return transcript
