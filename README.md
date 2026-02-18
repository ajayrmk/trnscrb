# trnscrb

Lightweight, fully offline meeting transcription for Claude Desktop.
A native macOS alternative to Granola — no cloud, no subscription.

## What it does

- Auto-detects meetings (Google Meet, Zoom, Slack Huddle, Teams, and more)
- Records your meeting audio (mic + system audio via BlackHole)
- Transcribes locally with Whisper (`small` model, Apple Silicon Metal)
- Identifies speakers with pyannote diarization
- Reads your calendar to auto-name meetings
- Saves every transcript as a plain `.txt` file in `~/meeting-notes/`
- Exposes everything to Claude Desktop via MCP so Claude can search, read, and enrich your notes

## Requirements

- macOS 13+
- Python 3.11+
- Apple Silicon recommended (M1/M2/M3) for fast local transcription

## Install

```bash
# One-liner bootstrap:
bash <(curl -fsSL https://raw.githubusercontent.com/ajayrmk/trnscrb/main/install.sh)

# Or if already cloned:
trnscrb install
```

The installer checks and optionally installs:
- BlackHole 2ch audio driver (via Homebrew)
- Python packages (faster-whisper, pyannote, rumps, sounddevice, mcp, …)
- HuggingFace token (for pyannote speaker diarization)
- Whisper `small` model (~500 MB, downloaded once)
- Claude Desktop MCP config entry
- Launch-at-login agent

## Usage

### Menu bar app
```bash
trnscrb start
```
A mic icon appears in your menu bar with **Auto-transcribe** on by default — it will
automatically start and stop transcribing when it detects a meeting.

Or manually: click → **Start Transcribing** / **Stop Transcribing**.

### From Claude Desktop
Once the MCP server is configured (done by `trnscrb install`), Claude has access to:

| Tool | What it does |
|---|---|
| `start_recording` | Begin capturing audio |
| `stop_recording` | Stop, transcribe, save |
| `recording_status` | Check progress |
| `get_last_transcript` | Most recent transcript |
| `list_transcripts` | All saved meetings |
| `get_transcript` | Full text of one meeting |
| `get_calendar_context` | Current calendar event |
| `enrich_transcript` | Summary + action items via Claude API |

### CLI
```bash
trnscrb list               # list all transcripts
trnscrb show <id>          # print a transcript
trnscrb enrich <id>        # run Claude LLM pass (needs ANTHROPIC_API_KEY)
trnscrb mic-status         # live mic activity monitor (debug)
trnscrb devices            # list audio input devices
```

## System audio (BlackHole setup)

After installing BlackHole:
1. Open **Audio MIDI Setup** (Spotlight → "Audio MIDI Setup")
2. Click **+** → **Create Multi-Output Device**
3. Check both **BlackHole 2ch** and **MacBook Pro Speakers**
4. Go to **System Settings → Sound → Output** → select the Multi-Output Device

## Transcript format

```
Meeting: Weekly Standup
Date:    2024-01-15 10:00
Duration:23:14

============================================================

[SPEAKER_00]
  00:12  Good morning everyone, let's get started.

[SPEAKER_01]
  00:18  Morning! I finished the auth PR yesterday.
```

After running `trnscrb enrich <id>`, speaker labels are replaced with inferred names
and a summary + action items block is appended.

## Privacy

All audio processing happens on your device. No data leaves your machine except:
- If you run `enrich`, the transcript text is sent to Claude API (Anthropic)
- Calendar access uses AppleScript locally — no network calls

## License

MIT
