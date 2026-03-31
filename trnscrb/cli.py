"""CLI entry point.

  trnscrb install   — smart dependency checker / installer
  trnscrb start     — launch the menu bar app
  trnscrb server    — start MCP server (Claude Desktop calls this)
  trnscrb list      — list saved transcripts
  trnscrb show <id> — print a transcript
  trnscrb enrich <id> — run Claude LLM pass on a transcript
  trnscrb watch     — headless auto-record watcher
  trnscrb devices   — list audio input devices
"""
import importlib.util
import json
from datetime import datetime
import subprocess
import sys
from pathlib import Path

import click

# Path to Claude Desktop's MCP config file
_CLAUDE_CONFIG = (
    Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
)


# ── CLI group ─────────────────────────────────────────────────────────────────

@click.group()
def cli():
    """Trnscrb — lightweight offline meeting transcription for Claude Desktop."""


# ── install ───────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--force", is_flag=True, help="Re-install packages even if already present.")
def install(force: bool):
    """Smart installer — checks each dependency and skips what's already installed."""
    click.echo()
    click.echo(click.style("Trnscrb Setup", bold=True))
    click.echo("=" * 42)

    # 1. Python version
    py_ok = sys.version_info >= (3, 11)
    _row("Python 3.11+", py_ok, sys.version.split()[0])
    if not py_ok:
        click.echo(click.style("  Python 3.11+ is required. Install from python.org.", fg="red"))
        sys.exit(1)

    click.echo()

    # 2. Xcode CLI tools + SCK audio capture helper
    xcode_ok = _xcode_cli_installed()
    _row("Xcode CLI Tools", xcode_ok, "needed to build audio capture helper")
    if not xcode_ok:
        click.echo("  Run: xcode-select --install")
        if click.confirm("  Install now?", default=True):
            _run(["xcode-select", "--install"])
            click.echo("  Complete the install dialog, then re-run: trnscrb install")
            sys.exit(0)

    sck_ok = _sck_binary_built()
    _row("SCK audio helper", sck_ok, "ScreenCaptureKit capture binary")
    if not sck_ok:
        click.echo("  Building audio capture helper…")
        if _build_sck_helper():
            click.echo(click.style("  Audio capture helper built.", fg="green"))
        else:
            click.echo(click.style("  Build failed. Check Xcode CLI tools.", fg="red"))

    click.echo()

    # 3. Python packages
    click.echo("  Python packages:")
    packages = {
        "rumps":           "rumps>=0.4.0",
        "sounddevice":     "sounddevice>=0.4.6",
        "faster_whisper":  "faster-whisper>=1.0.0",
        "pyannote.audio":  "pyannote.audio>=3.1",
        "mcp":             "mcp>=1.0.0",
        "anthropic":       "anthropic>=0.25",
        "scipy":           "scipy>=1.11",
        "numpy":           "numpy>=1.24",
    }
    to_install = []
    for import_name, pkg_spec in packages.items():
        installed = _pkg_installed(import_name) and not force
        _row(f"  {pkg_spec.split('>=')[0]}", installed, "  ", indent=4)
        if not installed:
            to_install.append(pkg_spec)

    if to_install:
        click.echo()
        if click.confirm(f"  Install {len(to_install)} missing package(s)?", default=True):
            _run([sys.executable, "-m", "pip", "install", "--quiet", *to_install])

    click.echo()

    # 4. HuggingFace token (for pyannote)
    hf_ok = bool(_get_hf_token())
    _row("HuggingFace token", hf_ok, "for pyannote speaker diarization")
    if not hf_ok:
        click.echo("  Get a free token at https://hf.co/settings/tokens")
        click.echo("  Accept model terms at https://hf.co/pyannote/speaker-diarization-3.1")
        token = click.prompt(
            "  Paste token (or press Enter to skip)",
            default="", show_default=False,
        )
        if token.strip():
            _save_hf_token(token.strip())
            click.echo(click.style("  Token saved to ~/.cache/huggingface/token", fg="green"))

    click.echo()

    # 5. Whisper model cache
    model_ok = _whisper_model_cached("small")
    _row("Whisper 'small' model", model_ok, "~500 MB, runs on Apple Silicon Metal")
    if not model_ok:
        if click.confirm("  Download now?", default=True):
            click.echo("  Downloading… (first time only, ~500 MB)")
            try:
                from faster_whisper import WhisperModel  # noqa: PLC0415
                WhisperModel("small", device="cpu")
                click.echo(click.style("  Model ready.", fg="green"))
            except Exception as e:
                click.echo(click.style(f"  Download failed: {e}", fg="yellow"))

    click.echo()

    # 6. Claude Desktop MCP config
    mcp_ok = _mcp_configured()
    _row("Claude Desktop MCP config", mcp_ok, str(_CLAUDE_CONFIG))
    if not mcp_ok:
        if click.confirm("  Add trnscrb to Claude Desktop config?", default=True):
            _write_mcp_config()
            click.echo(
                click.style("  Config updated. Restart Claude Desktop to apply.", fg="green")
            )

    # 7. Notes directory
    from trnscrb.storage import ensure_notes_dir
    folder = ensure_notes_dir()
    click.echo(f"\n  ✓ Notes folder: {folder}")

    click.echo()

    # 8. Permissions (mic + calendar + screen recording)
    click.echo("  Permissions (macOS will prompt if not yet granted):")
    click.echo()
    click.echo("  🎙  Microphone        — required to record audio")
    _request_mic_permission()
    click.echo("  📅  Calendar          — optional, used to auto-name meetings from your events")
    _request_calendar_permission()
    click.echo("  🖥  Screen Recording  — required for ScreenCaptureKit audio capture")
    _request_screen_recording_permission()

    click.echo()

    # 9. Login item — start trnscrb automatically on login
    login_ok = _login_item_exists()
    _row("Launch at login", login_ok, "auto-starts trnscrb when you log in")
    if not login_ok:
        if click.confirm("  Set up launch at login?", default=True):
            import shutil
            binary = shutil.which("trnscrb") or sys.executable
            if _setup_login_item(binary):
                click.echo(click.style("  Launch at login enabled.", fg="green"))
            else:
                click.echo(click.style("  Could not set up login item.", fg="yellow"))

    # 10. Default settings
    from trnscrb.settings import load as load_settings, save as save_settings
    settings = load_settings()
    if settings.get("auto_record") is not True:
        settings["auto_record"] = True
        save_settings(settings)
    click.echo("\n  ✓ Auto-record on by default")

    click.echo()
    click.echo("=" * 42)
    click.echo(click.style("Setup complete!", fg="green", bold=True))
    click.echo()
    click.echo("  trnscrb start    → launch menu bar app")
    click.echo("  trnscrb list     → list saved transcripts")
    click.echo()


# ── start ──────────────────────────────────────────────────────────────────────

@cli.command()
def start():
    """Launch the menu bar app."""
    from trnscrb.menu_bar import main
    main()


# ── server ────────────────────────────────────────────────────────────────────

@cli.command()
def server():
    """Start the MCP server (used internally by Claude Desktop)."""
    from trnscrb.mcp_server import main
    main()


# ── watch ─────────────────────────────────────────────────────────────────────

@cli.command()
def watch():
    """Watch for mic activity and auto-record meetings (headless, no menu bar)."""
    import signal
    from trnscrb.watcher import MicWatcher, WARMUP_SECS, GRACE_SECS
    from trnscrb import recorder as rec_module, transcriber, diarizer, storage
    from trnscrb.calendar_integration import get_current_or_upcoming_event

    _recorder_ref: list = [None]
    _started_ref:  list = [None]

    def on_start(meeting_name: str, bundle_id: str | None = None):
        click.echo(f"  🔴 Meeting detected: {meeting_name} — recording started")
        r = rec_module.Recorder(app_bundle_id=bundle_id)
        r.start()
        _recorder_ref[0] = r
        _started_ref[0]  = datetime.now()

    def on_stop():
        r          = _recorder_ref[0]
        started_at = _started_ref[0] or datetime.now()
        _recorder_ref[0] = None
        if not r:
            return
        click.echo("  ⏹  Meeting ended — transcribing…")
        audio_path = r.stop()
        if not audio_path:
            click.echo("  ⚠️  No audio captured.")
            return

        evt          = get_current_or_upcoming_event()
        meeting_name = evt["title"] if evt else f"meeting-{started_at.strftime('%H%M')}"

        try:
            segments = transcriber.transcribe(audio_path)
        except Exception as e:
            audio_path.unlink(missing_ok=True)
            click.echo(f"  ✗ Transcription failed: {e}")
            return

        import os
        hf_token = os.environ.get("HF_TOKEN")
        if not hf_token:
            tf = Path.home() / ".cache" / "huggingface" / "token"
            hf_token = tf.read_text().strip() if tf.exists() else None
        if hf_token and segments:
            try:
                diar     = diarizer.diarize(audio_path, hf_token)
                segments = diarizer.merge(segments, diar)
            except Exception:
                pass

        audio_path.unlink(missing_ok=True)
        text = storage.format_transcript(segments, started_at, meeting_name)
        path = storage.get_transcript_path(meeting_name, started_at)
        storage.save_transcript(path, text)
        click.echo(f"  ✓ Saved: {path.name}")

        from trnscrb.settings import get as get_setting
        if get_setting("auto_enrich"):
            try:
                from trnscrb.enricher import enrich_transcript
                from trnscrb.calendar_integration import get_current_or_upcoming_event
                result = enrich_transcript(text, calendar_event=get_current_or_upcoming_event())
                updated = (
                    result["enriched_transcript"]
                    + "\n\n" + "=" * 60 + "\n\n"
                    + result["enrichment"]
                )
                storage.save_transcript(path, updated)
                click.echo(f"  ✓ Enriched: {path.name}")
            except Exception as e:
                click.echo(f"  ⚠ Enrichment failed: {e}")

    watcher = MicWatcher(on_start=on_start, on_stop=on_stop)
    watcher.start()

    click.echo(f"Watching for mic activity (warmup={WARMUP_SECS}s, grace={GRACE_SECS}s).")
    click.echo("Press Ctrl-C to stop.\n")

    def _shutdown(sig, frame):
        click.echo("\nStopping watcher…")
        watcher.stop()
        if _recorder_ref[0]:
            on_stop()
        raise SystemExit(0)

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    import time
    while watcher.is_watching:
        time.sleep(1)


# ── list ──────────────────────────────────────────────────────────────────────

@cli.command(name="list")
def list_cmd():
    """List all saved meeting transcripts."""
    from trnscrb import storage
    transcripts = storage.list_transcripts()
    if not transcripts:
        click.echo("No transcripts found in ~/meeting-notes/")
        return
    for t in transcripts:
        size_kb = t["size"] // 1024 or 1
        click.echo(f"  {t['id']}  ({t['modified'][:16]})  {size_kb} KB")


# ── show ──────────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("transcript_id")
def show(transcript_id: str):
    """Print a transcript to stdout."""
    from trnscrb import storage
    text = storage.read_transcript(transcript_id)
    if text is None:
        click.echo(f"Transcript '{transcript_id}' not found.", err=True)
        sys.exit(1)
    click.echo(text)


# ── enrich ────────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("transcript_id")
def enrich(transcript_id: str):
    """Run a Claude LLM pass on a transcript: summary, action items, speaker names."""
    from trnscrb import storage
    from trnscrb.enricher import enrich_transcript
    from trnscrb.calendar_integration import get_current_or_upcoming_event

    text = storage.read_transcript(transcript_id)
    if text is None:
        click.echo(f"Transcript '{transcript_id}' not found.", err=True)
        sys.exit(1)

    click.echo("Running Claude enrichment…")
    evt = get_current_or_upcoming_event()
    result = enrich_transcript(text, calendar_event=evt)

    # Overwrite file with resolved speaker names + enrichment appended
    path = storage.NOTES_DIR / f"{transcript_id}.txt"
    updated = result["enriched_transcript"] + "\n\n" + "=" * 60 + "\n\n" + result["enrichment"]
    storage.save_transcript(path, updated)

    click.echo(result["enrichment"])
    click.echo(f"\nTranscript updated at {path}")


# ── devices ───────────────────────────────────────────────────────────────────

@cli.command()
def icons():
    """Generate menu bar icons (mic PNG). Run once after install."""
    from trnscrb.icon import generate_icons_cli
    generate_icons_cli()


@cli.command(name="mic-status")
def mic_status():
    """Check live mic activity and which meeting app is detected."""
    import time
    from trnscrb.watcher import is_mic_in_use, detect_meeting, WARMUP_SECS, GRACE_SECS

    active = is_mic_in_use()
    status = click.style("IN USE 🔴", fg="red") if active else click.style("idle  ⚪", fg="white")
    click.echo(f"\n  Microphone: {status}")

    if active:
        name, bundle_id = detect_meeting()
        bundle_str = f" ({bundle_id})" if bundle_id else ""
        click.echo(f"  Detected app: {name}{bundle_str}")
    click.echo(f"\n  Watcher thresholds: warmup={WARMUP_SECS}s  grace={GRACE_SECS}s  min_save={30}s")
    click.echo()
    click.echo("  Watching for 10 seconds (press Ctrl-C to stop early)…")
    for i in range(10):
        time.sleep(1)
        active = is_mic_in_use()
        mark = "🔴" if active else "⚪"
        click.echo(f"  {i+1:2d}s  {mark}", nl=True)
    click.echo()


@cli.command()
def devices():
    """List available audio input devices."""
    import sounddevice as sd
    devs = sd.query_devices()
    found = False
    for i, d in enumerate(devs):
        if d["max_input_channels"] > 0:
            found = True
            click.echo(f"  [{i}] {d['name']}  {d['max_input_channels']}ch")
    if not found:
        click.echo("No input devices found.")


# ── helpers ───────────────────────────────────────────────────────────────────

def _row(label: str, ok: bool, detail: str = "", indent: int = 2):
    mark = click.style("✓", fg="green") if ok else click.style("✗", fg="red")
    status = click.style("ok", fg="green") if ok else click.style("missing", fg="yellow")
    pad = " " * indent
    click.echo(f"{pad}{mark} {label:<30} {status}  {detail}")


def _pkg_installed(import_name: str) -> bool:
    return importlib.util.find_spec(import_name.split(".")[0]) is not None


def _xcode_cli_installed() -> bool:
    """Check if Xcode Command Line Tools are installed."""
    try:
        result = subprocess.run(
            ["xcode-select", "-p"],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


def _sck_binary_built() -> bool:
    """Check if the sck-capture binary exists."""
    from trnscrb.sck import find_binary
    return find_binary() is not None


def _build_sck_helper() -> bool:
    """Build the sck-capture Swift helper and install to ~/.local/share/trnscrb/."""
    # Bundled inside the Python package (works for pip/uv installs)
    swift_dir = Path(__file__).resolve().parent / "sck-capture"
    if not swift_dir.exists():
        # Fallback: repo root (development checkout)
        swift_dir = Path(__file__).resolve().parent.parent / "swift" / "sck-capture"
    if not swift_dir.exists():
        click.echo(click.style(f"  Swift source not found at {swift_dir}", fg="red"))
        return False
    try:
        result = subprocess.run(
            ["swift", "build", "-c", "release"],
            cwd=str(swift_dir),
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            click.echo(result.stderr[-500:] if result.stderr else "Unknown error")
            return False

        # Copy binary to install location
        built = swift_dir / ".build" / "release" / "sck-capture"
        if not built.exists():
            return False

        install_dir = Path.home() / ".local" / "share" / "trnscrb"
        install_dir.mkdir(parents=True, exist_ok=True)
        dest = install_dir / "sck-capture"
        import shutil
        shutil.copy2(str(built), str(dest))
        dest.chmod(0o755)
        return True
    except Exception as e:
        click.echo(click.style(f"  Build error: {e}", fg="red"))
        return False


def _get_hf_token() -> str | None:
    import os
    token = os.environ.get("HF_TOKEN")
    if token:
        return token
    token_file = Path.home() / ".cache" / "huggingface" / "token"
    if token_file.exists():
        return token_file.read_text().strip() or None
    return None


def _save_hf_token(token: str):
    d = Path.home() / ".cache" / "huggingface"
    d.mkdir(parents=True, exist_ok=True)
    (d / "token").write_text(token)


def _whisper_model_cached(size: str) -> bool:
    # faster-whisper stores models under ~/.cache/huggingface/hub/models--Systran--faster-whisper-*
    hf_hub = Path.home() / ".cache" / "huggingface" / "hub"
    if any(hf_hub.glob(f"models--Systran--faster-whisper-{size}")):
        return True
    # also check ct2 local cache
    ct2_cache = Path.home() / ".cache" / "faster_whisper"
    return ct2_cache.exists() and any(ct2_cache.glob(f"*{size}*"))


def _mcp_configured() -> bool:
    if not _CLAUDE_CONFIG.exists():
        return False
    try:
        config = json.loads(_CLAUDE_CONFIG.read_text())
        return "trnscrb" in config.get("mcpServers", {})
    except Exception:
        return False


def _write_mcp_config():
    config: dict = {}
    if _CLAUDE_CONFIG.exists():
        try:
            config = json.loads(_CLAUDE_CONFIG.read_text())
        except Exception:
            pass
    # Prefer the installed binary on PATH; fall back to python -m
    import shutil
    binary = shutil.which("trnscrb") or sys.executable
    if binary.endswith("trnscrb"):
        cmd_entry = {"command": binary, "args": ["server"]}
    else:
        cmd_entry = {"command": binary, "args": ["-m", "trnscrb.mcp_server"]}

    config.setdefault("mcpServers", {})
    config["mcpServers"]["trnscrb"] = cmd_entry
    _CLAUDE_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    _CLAUDE_CONFIG.write_text(json.dumps(config, indent=2))


def _run(cmd: list[str]):
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        click.echo(click.style(f"  Command failed: {e}", fg="yellow"))
    except FileNotFoundError:
        click.echo(click.style(f"  Not found: {cmd[0]}", fg="yellow"))


# ── permission helpers ────────────────────────────────────────────────────────

def _request_mic_permission() -> None:
    """Briefly open the audio input stream to trigger the macOS mic permission dialog."""
    try:
        import sounddevice as sd
        import time
        stream = sd.InputStream(channels=1, samplerate=16000, dtype="float32")
        stream.start()
        time.sleep(0.3)
        stream.stop()
        stream.close()
        click.echo(click.style("    ✓ Microphone access granted", fg="green"))
    except Exception as e:
        click.echo(click.style(f"    ⚠  Microphone: {e}", fg="yellow"))


def _request_calendar_permission() -> None:
    """Call Calendar via AppleScript to trigger the macOS calendar permission dialog."""
    try:
        from trnscrb.calendar_integration import get_current_or_upcoming_event
        get_current_or_upcoming_event()
        click.echo(click.style("    ✓ Calendar access granted (or skipped)", fg="green"))
    except Exception as e:
        click.echo(click.style(f"    ⚠  Calendar: {e}", fg="yellow"))


def _request_screen_recording_permission() -> None:
    """Check Screen Recording permission (required for ScreenCaptureKit)."""
    try:
        from trnscrb.screen_capture import check_permission, request_permission
        if check_permission():
            click.echo(click.style("    ✓ Screen Recording access granted", fg="green"))
        else:
            request_permission()
            click.echo(click.style(
                "    ⚠  Screen Recording: grant access in System Settings → "
                "Privacy & Security → Screen Recording",
                fg="yellow",
            ))
    except Exception as e:
        click.echo(click.style(f"    ⚠  Screen Recording: {e}", fg="yellow"))


# ── login item helpers ────────────────────────────────────────────────────────

_LAUNCH_AGENT_LABEL = "io.trnscrb.app"
_PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{_LAUNCH_AGENT_LABEL}.plist"


def _login_item_exists() -> bool:
    return _PLIST_PATH.exists()


def _setup_login_item(binary_path: str) -> bool:
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{_LAUNCH_AGENT_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{binary_path}</string>
        <string>start</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
    <key>StandardOutPath</key>
    <string>/tmp/trnscrb.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/trnscrb.err</string>
</dict>
</plist>
"""
    try:
        _PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
        _PLIST_PATH.write_text(plist)
        # Load it for the current session (unload first in case it was previously loaded)
        subprocess.run(
            ["launchctl", "unload", str(_PLIST_PATH)],
            capture_output=True,
        )
        subprocess.run(
            ["launchctl", "load", str(_PLIST_PATH)],
            capture_output=True, check=True,
        )
        return True
    except Exception:
        return False
