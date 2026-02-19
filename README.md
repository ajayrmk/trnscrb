# trnscrb

> Offline meeting transcription for macOS — no cloud, no subscription.

trnscrb lives in your menu bar, listens for meetings, transcribes them locally with Whisper, and makes every transcript searchable from Claude Desktop via MCP.

---

## Install

```bash
brew tap ajayrmk/tap
brew install trnscrb
trnscrb install
```

Or with `pip` / `uv`:

```bash
pip install trnscrb && trnscrb install
uv tool install trnscrb && trnscrb install
```

`trnscrb install` is a guided setup that handles:

- BlackHole 2ch audio driver (captures system audio alongside mic)
- HuggingFace token for speaker diarization (pyannote)
- Whisper `small` model download (~500 MB, one-time)
- Claude Desktop MCP config
- Launch-at-login agent

---

## Quick start

```bash
trnscrb start       # launch the menu bar app
```

With **Auto-transcribe** on (the default), trnscrb detects when a meeting starts — Google Meet, Zoom, Slack Huddle, Teams, FaceTime — and begins recording automatically. When the meeting ends, it stops, transcribes, and saves.

You can also trigger manually from the menu bar: **Start Transcribing / Stop Transcribing**.

---

## How it works

| Step | What happens |
|---|---|
| Meeting detected | Mic active for 5 s + meeting app found |
| Recording | Audio captured via mic or BlackHole (system + mic) |
| Transcription | Whisper `small` model, runs locally on Apple Silicon |
| Diarization | Speaker labels via pyannote (needs HuggingFace token) |
| Saved | Plain `.txt` in `~/meeting-notes/` |

---

## Claude Desktop integration

After `trnscrb install`, Claude Desktop has these tools available:

| Tool | Description |
|---|---|
| `start_recording` | Start capturing audio |
| `stop_recording` | Stop and transcribe in the background |
| `recording_status` | Check if recording or transcribing |
| `get_last_transcript` | Fetch the most recent transcript |
| `list_transcripts` | List all saved meetings |
| `get_transcript` | Read a specific transcript |
| `get_calendar_context` | Current or upcoming calendar event |
| `enrich_transcript` | Add summary + action items via Claude API |

---

## CLI

```bash
trnscrb start               # launch menu bar app
trnscrb install             # guided setup / re-check dependencies
trnscrb list                # list saved transcripts
trnscrb show <id>           # print a transcript
trnscrb enrich <id>         # summarise + action items (needs ANTHROPIC_API_KEY)
trnscrb mic-status          # live mic activity monitor — useful for debugging
trnscrb devices             # list audio input devices
trnscrb watch               # headless auto-transcribe, no menu bar
```

---

## System audio with BlackHole

To capture both your mic and the other participants' audio:

1. Install BlackHole via `trnscrb install` (or `brew install blackhole-2ch`)
2. Open **Audio MIDI Setup** → **+** → **Create Multi-Output Device**
3. Check **BlackHole 2ch** and **MacBook Pro Speakers**
4. **System Settings → Sound → Output** → select the Multi-Output Device

trnscrb auto-detects BlackHole and uses it when available. Without it, only your mic is recorded.

---

## Transcript format

```
Meeting: Weekly Standup
Date:    2025-02-18 10:00
Duration:23:14

============================================================

[SPEAKER_00]
  00:12  Good morning, let's get started.

[SPEAKER_01]
  00:18  Morning! I finished the auth PR yesterday.
```

Running `trnscrb enrich <id>` replaces `SPEAKER_00` / `SPEAKER_01` with inferred names and appends a summary and action items block.

---

## Requirements

- macOS 13 or later
- Python 3.11+
- Apple Silicon (M1/M2/M3/M4) recommended — Whisper runs on Metal

---

## Privacy

Everything runs on your machine. No audio or transcripts leave your device unless you explicitly run `enrich`, which sends the transcript text to the Claude API.

---

## License

MIT
