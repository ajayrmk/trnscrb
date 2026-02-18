"""Transcript file storage — saves and reads .txt files from ~/meeting-notes/."""
from pathlib import Path
from datetime import datetime

NOTES_DIR = Path.home() / "meeting-notes"


def ensure_notes_dir() -> Path:
    NOTES_DIR.mkdir(exist_ok=True)
    return NOTES_DIR


def get_transcript_path(meeting_name: str, started_at: datetime) -> Path:
    ensure_notes_dir()
    date_str = started_at.strftime("%Y-%m-%d_%H-%M")
    safe_name = meeting_name.replace(" ", "-").replace("/", "-")[:50]
    return NOTES_DIR / f"{date_str}_{safe_name}.txt"


def save_transcript(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def list_transcripts() -> list[dict]:
    ensure_notes_dir()
    files = sorted(NOTES_DIR.glob("*.txt"), reverse=True)
    return [
        {
            "id": f.stem,
            "path": str(f),
            "name": f.name,
            "size": f.stat().st_size,
            "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
        }
        for f in files
    ]


def read_transcript(transcript_id: str) -> str | None:
    path = NOTES_DIR / f"{transcript_id}.txt"
    return path.read_text(encoding="utf-8") if path.exists() else None


def format_transcript(segments: list[dict], started_at: datetime, meeting_name: str) -> str:
    if segments:
        duration = _fmt_time(segments[-1]["end"])
    else:
        duration = "00:00"

    lines = [
        f"Meeting: {meeting_name}",
        f"Date:    {started_at.strftime('%Y-%m-%d %H:%M')}",
        f"Duration:{duration}",
        "",
        "=" * 60,
        "",
    ]
    current_speaker = None
    for seg in segments:
        speaker = seg.get("speaker") or "Unknown"
        if speaker != current_speaker:
            if current_speaker is not None:
                lines.append("")
            lines.append(f"[{speaker}]")
            current_speaker = speaker
        lines.append(f"  {_fmt_time(seg['start'])}  {seg['text']}")

    return "\n".join(lines)


def _fmt_time(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"
