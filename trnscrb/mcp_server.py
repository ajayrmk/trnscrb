"""MCP server exposing trnscrb tools to Claude Desktop.

Runs as a stdio server — Claude Desktop starts it automatically via the
entry in claude_desktop_config.json.

Tools available to Claude:
  start_recording        — begin audio capture
  stop_recording         — stop immediately, process in background
  recording_status       — check if recording / if processing is done
  get_last_transcript    — get the transcript from the most recent stop
  get_current_transcript — live partial transcript during recording
  list_transcripts       — all saved meeting files
  get_transcript         — full text of one transcript
  get_calendar_context   — current/upcoming calendar event
  enrich_transcript      — post-call summary + action items via Claude API
"""
import os
import json
import threading
from datetime import datetime
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from trnscrb import storage, recorder as rec_module, diarizer, transcriber
from trnscrb.calendar_integration import get_current_or_upcoming_event

mcp = FastMCP("trnscrb")

# ── Shared state ──────────────────────────────────────────────────────────────
_recorder: rec_module.Recorder | None = None
_recording_started_at: datetime | None = None
_state_lock = threading.Lock()

# Background processing state
_processing = False          # True while transcription/diarization is running
_last_result: str | None = None   # last stop_recording outcome (path + preview)
_last_error: str | None = None    # last processing error if any


# ── Tools ─────────────────────────────────────────────────────────────────────

@mcp.tool()
def start_recording() -> str:
    """Start capturing audio for a meeting transcript."""
    global _recorder, _recording_started_at
    with _state_lock:
        if _recorder and _recorder.is_recording:
            return "Already recording."
        device = rec_module.Recorder.find_blackhole_device()
        _recorder = rec_module.Recorder(device=device)
        _recorder.start()
        _recording_started_at = datetime.now()
    source = "BlackHole (system + mic)" if device is not None else "built-in mic"
    return f"Recording started at {_recording_started_at.strftime('%H:%M')} using {source}."


@mcp.tool()
def stop_recording(meeting_name: str = "") -> str:
    """
    Stop the recording immediately and kick off transcription in the background.
    Returns right away — use recording_status or get_last_transcript to get the result.

    Args:
        meeting_name: Optional name. Defaults to calendar event title or timestamp.
    """
    global _recorder, _recording_started_at, _processing, _last_result, _last_error
    with _state_lock:
        if not _recorder or not _recorder.is_recording:
            return "Not currently recording."
        started_at = _recording_started_at or datetime.now()
        audio_path = _recorder.stop()   # stops the stream, returns WAV path
        _recorder = None

    if not audio_path:
        return "Recording stopped but no audio was captured."

    # Resolve name before background thread (calendar call is fast)
    if not meeting_name:
        evt = get_current_or_upcoming_event()
        meeting_name = evt["title"] if evt else f"meeting-{started_at.strftime('%H%M')}"

    _processing = True
    _last_result = None
    _last_error = None

    thread = threading.Thread(
        target=_process_audio,
        args=(audio_path, started_at, meeting_name),
        daemon=True,
    )
    thread.start()

    duration_s = int((datetime.now() - started_at).total_seconds())
    return (
        f"Recording stopped. {duration_s}s of audio captured for \"{meeting_name}\".\n"
        f"Transcription is running in the background.\n"
        f"Ask me for `recording_status` to check progress, or `get_last_transcript` once done."
    )


@mcp.tool()
def recording_status() -> str:
    """Check whether a recording is active or background transcription is in progress."""
    with _state_lock:
        is_recording = bool(_recorder and _recorder.is_recording)
        started_at = _recording_started_at

    if is_recording and started_at:
        elapsed = int((datetime.now() - started_at).total_seconds())
        m, s = divmod(elapsed, 60)
        return f"Recording in progress — {m}m {s}s elapsed."

    if _processing:
        return "Transcription in progress — processing audio, please wait."

    if _last_error:
        return f"Last transcription failed: {_last_error}"

    if _last_result:
        return f"Transcription complete. Use get_last_transcript to read it."

    return "Idle — no active recording or pending transcription."


@mcp.tool()
def get_last_transcript() -> str:
    """Return the transcript from the most recently completed recording."""
    if _processing:
        return "Still transcribing — check back in a moment."
    if _last_error:
        return f"Transcription failed: {_last_error}"
    if _last_result:
        return _last_result
    return "No transcript available yet. Start and stop a recording first."


@mcp.tool()
def get_current_transcript() -> str:
    """Return whatever has been transcribed so far during an active recording (not yet available — live transcription coming soon)."""
    with _state_lock:
        is_recording = bool(_recorder and _recorder.is_recording)
    if not is_recording:
        return "Not currently recording."
    return "Recording in progress. Live transcript is not yet available — stop the recording to get the full transcript."


@mcp.tool()
def list_transcripts() -> str:
    """List all saved meeting transcripts (most recent first)."""
    transcripts = storage.list_transcripts()
    if not transcripts:
        return "No transcripts found in ~/meeting-notes/"
    lines = [f"{t['id']}  ({t['modified'][:16]})" for t in transcripts[:30]]
    return "\n".join(lines)


@mcp.tool()
def get_transcript(transcript_id: str) -> str:
    """
    Return the full text of a saved transcript.

    Args:
        transcript_id: The filename stem (e.g. 2024-01-15_14-30_standup).
    """
    text = storage.read_transcript(transcript_id)
    if text is None:
        return f"Transcript '{transcript_id}' not found."
    return text


@mcp.tool()
def get_calendar_context() -> str:
    """Return the current or next upcoming calendar event for meeting context."""
    evt = get_current_or_upcoming_event()
    if not evt:
        return "No current or upcoming calendar events found."
    return json.dumps(evt, indent=2)


@mcp.tool()
def enrich_transcript(transcript_id: str) -> str:
    """
    Run a Claude LLM pass on a saved transcript to produce a summary,
    action items, and inferred speaker names. Requires ANTHROPIC_API_KEY.

    Args:
        transcript_id: The filename stem of the transcript to enrich.
    """
    text = storage.read_transcript(transcript_id)
    if text is None:
        return f"Transcript '{transcript_id}' not found."

    if not os.environ.get("ANTHROPIC_API_KEY"):
        return "ANTHROPIC_API_KEY not set — cannot call Claude API."

    from trnscrb.enricher import enrich_transcript as _enrich
    evt = get_current_or_upcoming_event()
    result = _enrich(text, calendar_event=evt)

    path = storage.NOTES_DIR / f"{transcript_id}.txt"
    if path.exists():
        updated = (
            result["enriched_transcript"]
            + "\n\n" + "=" * 60 + "\n\n"
            + result["enrichment"]
        )
        storage.save_transcript(path, updated)

    return result["enrichment"]


# ── Background processing ─────────────────────────────────────────────────────

def _process_audio(audio_path: Path, started_at: datetime, meeting_name: str) -> None:
    global _processing, _last_result, _last_error
    try:
        segments = transcriber.transcribe(audio_path)

        hf_token = _read_hf_token()
        if hf_token and segments:
            try:
                diar = diarizer.diarize(audio_path, hf_token)
                segments = diarizer.merge(segments, diar)
            except Exception:
                pass  # fall back to unlabeled segments

        audio_path.unlink(missing_ok=True)

        transcript_text = storage.format_transcript(segments, started_at, meeting_name)
        path = storage.get_transcript_path(meeting_name, started_at)
        storage.save_transcript(path, transcript_text)

        preview = transcript_text[:800] + ("…" if len(transcript_text) > 800 else "")
        _last_result = f"Saved: {path.name}\n\n{preview}"
    except Exception as e:
        audio_path.unlink(missing_ok=True)
        _last_error = str(e)
    finally:
        _processing = False


# ── Helpers ───────────────────────────────────────────────────────────────────

def _read_hf_token() -> str | None:
    token = os.environ.get("HF_TOKEN")
    if token:
        return token
    token_file = Path.home() / ".cache" / "huggingface" / "token"
    if token_file.exists():
        return token_file.read_text().strip() or None
    return None


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
