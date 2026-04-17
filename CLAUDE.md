# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

trnscrb is a macOS menu bar app for offline meeting transcription. It auto-detects meetings, records audio, transcribes locally with Whisper, diarizes speakers with pyannote, and exposes transcripts to Claude Desktop via MCP.

## Development commands

```bash
# Install in development mode
uv add --editable .

# Run the menu bar app
trnscrb start

# Run headless auto-transcribe (no UI)
trnscrb watch

# Start the MCP server (used by Claude Desktop)
trnscrb server

# List/show transcripts
trnscrb list
trnscrb show <id>

# Run guided setup (dependencies, models, MCP config)
trnscrb install
```

No test suite or linter is configured.

## Deploying changes

The menu bar app runs as a launchd service (`io.trnscrb.app`, plist at `~/Library/LaunchAgents/io.trnscrb.app.plist`) with `KeepAlive: true`. To deploy code changes:

```bash
uv pip install -e .              # install updated code
launchctl stop io.trnscrb.app    # launchd auto-restarts it with KeepAlive
```

Do NOT launch `trnscrb start` manually — launchd manages the lifecycle. Logs: `/tmp/trnscrb.log`, `/tmp/trnscrb.err`.

## Architecture

The pipeline is linear: **record → transcribe → diarize → merge → format → save → (optionally enrich)**.

### Key modules

- **cli.py** — Click-based CLI entry point (`trnscrb` command). Uses lazy imports to avoid startup latency.
- **menu_bar.py** — rumps-based macOS menu bar app with a 4-state machine: `idle → watching → recording → transcribing`. Transcription always runs in a background thread.
- **mcp_server.py** — FastMCP stdio server exposing 10 tools to Claude Desktop. Manages global state (`_recorder`, `_processing` flag, `_last_result`) across tool calls. Transcription runs in a daemon thread; tools return immediately.
- **watcher.py** — The most complex module. Detects meetings via CoreAudio mic polling (1s interval) + meeting app detection (AppleScript for browser tabs, process list for native apps). Internal state machine: `idle → warming → recording → cooling` with warmup/grace periods to avoid false positives.
- **recorder.py** — Dual-source audio capture: ScreenCaptureKit (via Swift helper) for meeting app audio + sounddevice for mic. Mixes both streams on stop. Auto-restarts mic stream on BLE device changes.
- **sck.py** — Python subprocess wrapper for the `sck-capture` Swift helper binary. Reads float32 PCM from stdout, waits for READY on stderr.
- **screen_capture.py** — ctypes bindings to CoreGraphics for Screen Recording permission check/request.
- **transcriber.py** — Whisper model loaded as a thread-safe singleton (lazy, with lock). Runs on Metal GPU via `device="auto"`.
- **diarizer.py** — pyannote speaker diarization. Requires HuggingFace token (`HF_TOKEN` env var or `~/.cache/huggingface/token`). Runs on MPS if available.
- **storage.py** — Saves transcripts as plain text to `~/meeting-notes/YYYY-MM-DD_HH-MM_name.txt`.
- **enricher.py** — Calls Claude API (claude-sonnet-4-6) to add summary, action items, and infer speaker names. Requires `ANTHROPIC_API_KEY`.
- **settings.py** — Persistent JSON at `~/.config/trnscrb/settings.json`. Keys: `auto_record` (bool), `model_size` (str, default "small").
- **calendar_integration.py** — AppleScript wrapper querying macOS Calendar for current/upcoming events.

### Threading model

- Audio recording callback buffers frames under a lock (non-blocking)
- Transcription always runs in a background thread (both menu_bar and MCP server)
- Watcher runs in a separate daemon thread polling CoreAudio every 1s
- Whisper model is a guarded singleton — loaded once on first use
- UI state machine ensures only one transcription runs at a time

### Watcher detection layers

1. **CoreAudio polling** — detects any process using the mic (1s interval)
2. **App detection** — checks native meeting apps (Zoom, Slack, Teams, etc.) and browser tabs (Chrome/Safari for Meet/Teams/Zoom URLs)
3. **Calendar fallback** — uses event title as meeting name if no app detected
4. **Final fallback** — `meeting-HHMM` timestamp

Key constants: `WARMUP_SECS=5`, `GRACE_SECS=5`, `MIN_SAVE_SECS=30`, `APP_POLL_EVERY=4`

## Important details

- macOS 14+ only — the watcher's stop-detection relies on the per-process
  CoreAudio API (`kAudioHardwarePropertyProcessObjectList`), which is Sonoma+
  (CoreAudio, ScreenCaptureKit, AppleScript, rumps menu bar)
- Python 3.11+, Apple Silicon recommended (Metal GPU acceleration)
- Requires Xcode CLI tools to build the `sck-capture` Swift helper (built during `trnscrb install`)
- The Swift helper source is in `swift/sck-capture/`; binary installs to `~/.local/share/trnscrb/sck-capture`
- Temp WAV files are cleaned up after transcription completes
- Diarization merges with transcription by timestamp overlap — can fail silently on very short/quiet audio
- The `docs/` folder contains the static marketing site, not project documentation
