"""macOS menu bar app (rumps).

States:
  idle        — mic icon, Start enabled, Stop disabled
  watching    — mic icon (auto-record on, listening)
  recording   — red icon, Start disabled, Stop enabled

Transcription runs in background threads and does not block new recordings.
The menu shows "Transcribing N meeting(s)…" while jobs are pending.
"""
import logging
import os
import subprocess
import threading
from datetime import datetime
from pathlib import Path

log = logging.getLogger("trnscrb")

import rumps

from trnscrb import recorder as rec_module, transcriber, diarizer, storage
from trnscrb.calendar_integration import get_current_or_upcoming_event
from trnscrb.icon import icon_path, generate_icons
from trnscrb.watcher import MicWatcher, MIN_SAVE_SECS
from trnscrb.settings import get as get_setting, put as put_setting, settings_file

_EMOJI_IDLE      = "🎙"
_EMOJI_RECORDING = "🔴"


def _notify(title: str, subtitle: str, message: str = "") -> None:
    """Send a macOS notification via osascript (rumps uses deprecated NSUserNotification)."""
    def _esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"')

    if message:
        script = f'display notification "{_esc(message)}" with title "{_esc(title)}" subtitle "{_esc(subtitle)}"'
    else:
        script = f'display notification "{_esc(subtitle)}" with title "{_esc(title)}"'
    subprocess.Popen(["osascript", "-e", script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class TrnscrbApp(rumps.App):
    def __init__(self):
        # Materialise missing defaults into the settings file so every
        # user-customisable key is visible when they open it from the menu.
        settings_file()

        try:
            generate_icons()
        except Exception:
            pass

        idle_icon = icon_path(recording=False)
        super().__init__(
            "Trnscrb",
            icon=idle_icon,
            title=None if idle_icon else _EMOJI_IDLE,
            quit_button=None,
            template=True,
        )

        # Keep direct references so we can retitle without re-lookup
        self._start_item = rumps.MenuItem("Start Transcribing", callback=self.start_recording)
        self._stop_item  = rumps.MenuItem("Stop Transcribing",  callback=None)
        self._auto_item   = rumps.MenuItem("Auto-transcribe: Off", callback=self.toggle_auto_record)
        self._enrich_item    = rumps.MenuItem("Auto-enrich: Off", callback=self.toggle_auto_enrich)
        self._integrate_item = rumps.MenuItem("Auto-integrate notes: Off", callback=self.toggle_auto_integrate)

        self.menu = [
            self._start_item,
            self._stop_item,
            None,
            self._auto_item,
            self._enrich_item,
            self._integrate_item,
            None,
            rumps.MenuItem("Open Notes Folder", callback=self.open_folder),
            rumps.MenuItem("Open Settings File", callback=self.open_settings),
            rumps.MenuItem("Reload Settings", callback=self.reload_settings),
            None,
            rumps.MenuItem("Quit", callback=self.quit_app),
        ]

        self._recorder:   rec_module.Recorder | None = None
        self._started_at: datetime | None = None
        self._watcher:    MicWatcher | None = None
        self._bg_jobs:    int = 0   # number of background transcription jobs
        self._bg_lock     = threading.Lock()

        self._set_state("idle")

        if get_setting("auto_record"):
            self._start_watcher()
            self._auto_item.title = "Auto-transcribe: On ✓"

        if get_setting("auto_enrich"):
            self._enrich_item.title = "Auto-enrich: On ✓"

        if get_setting("auto_integrate"):
            self._integrate_item.title = "Auto-integrate notes: On ✓"

    # ── watcher ───────────────────────────────────────────────────────────────

    def _start_watcher(self):
        self._watcher = MicWatcher(on_start=self._auto_start, on_stop=self._auto_stop)
        self._watcher.start()
        if not (self._recorder and self._recorder.is_recording):
            self._set_icon_state("watching")

    # ── manual controls ───────────────────────────────────────────────────────

    def start_recording(self, _):
        if self._recorder and self._recorder.is_recording:
            return
        self._do_start()

    def stop_recording(self, _):
        if not self._recorder or not self._recorder.is_recording:
            return
        self._do_stop()

    def toggle_auto_record(self, sender):
        if self._watcher and self._watcher.is_watching:
            self._watcher.stop()
            self._watcher = None
            sender.title = "Auto-transcribe: Off"
            put_setting("auto_record", False)
            if not (self._recorder and self._recorder.is_recording):
                self._set_icon_state("idle")
            _notify("Trnscrb", "Auto-transcribe off", "")
        else:
            self._start_watcher()
            sender.title = "Auto-transcribe: On ✓"
            put_setting("auto_record", True)
            _notify("Trnscrb", "Auto-transcribe on",
                               "Will start when mic is active for 5+ seconds")

    def toggle_auto_enrich(self, sender):
        if get_setting("auto_enrich"):
            sender.title = "Auto-enrich: Off"
            put_setting("auto_enrich", False)
            _notify("Trnscrb", "Auto-enrich off", "")
        else:
            sender.title = "Auto-enrich: On ✓"
            put_setting("auto_enrich", True)
            _notify("Trnscrb", "Auto-enrich on",
                               "Transcripts will be enriched with summary and action items")

    def toggle_auto_integrate(self, sender):
        if get_setting("auto_integrate"):
            sender.title = "Auto-integrate notes: Off"
            put_setting("auto_integrate", False)
            rumps.notification("Trnscrb", "Auto-integrate off", "")
        else:
            sender.title = "Auto-integrate notes: On ✓"
            put_setting("auto_integrate", True)
            rumps.notification("Trnscrb", "Auto-integrate on",
                               "Transcripts will be integrated into notes via Claude Code")

    def open_folder(self, _):
        subprocess.run(["open", str(storage.ensure_notes_dir())])

    def open_settings(self, _):
        subprocess.run(["open", str(settings_file())])

    def reload_settings(self, _):
        """Re-read the settings file and re-sync menu + watcher state."""
        auto_record    = bool(get_setting("auto_record"))
        auto_enrich    = bool(get_setting("auto_enrich"))
        auto_integrate = bool(get_setting("auto_integrate"))

        watcher_on = bool(self._watcher and self._watcher.is_watching)
        if auto_record and not watcher_on:
            self._start_watcher()
        elif not auto_record and watcher_on:
            self._watcher.stop()
            self._watcher = None
            if not (self._recorder and self._recorder.is_recording):
                self._set_icon_state("idle")

        self._auto_item.title      = "Auto-transcribe: On ✓"      if auto_record    else "Auto-transcribe: Off"
        self._enrich_item.title    = "Auto-enrich: On ✓"          if auto_enrich    else "Auto-enrich: Off"
        self._integrate_item.title = "Auto-integrate notes: On ✓" if auto_integrate else "Auto-integrate notes: Off"

        log.info("Settings reloaded")
        _notify("Trnscrb", "Settings reloaded", "")

    def quit_app(self, _):
        if self._watcher:
            self._watcher.stop()
        if self._recorder and self._recorder.is_recording:
            self._recorder.stop()
        rumps.quit_application()

    # ── shared start / stop ───────────────────────────────────────────────────

    def _do_start(self, meeting_name: str = "", bundle_id: str | None = None):
        if not meeting_name:
            evt = get_current_or_upcoming_event()
            meeting_name = evt["title"] if evt else ""

        self._recorder   = rec_module.Recorder(app_bundle_id=bundle_id)
        self._started_at = datetime.now()
        self._recorder.start()
        self._set_state("recording")

        source = self._recorder.audio_source_description
        label  = f" — {meeting_name}" if meeting_name else ""
        _notify("Trnscrb", f"Transcription started{label}", f"via {source}")

    def _do_stop(self):
        started_at     = self._started_at or datetime.now()
        recorder       = self._recorder
        self._recorder = None

        # Stop capture synchronously so the mic/SCK are released before
        # a new recording can start.  Only transcription runs in background.
        audio_path = recorder.stop()

        with self._bg_lock:
            self._bg_jobs += 1
        self._restore_idle()

        try:
            threading.Thread(
                target=self._process, args=(audio_path, started_at), daemon=True
            ).start()
        except Exception:
            with self._bg_lock:
                self._bg_jobs = max(0, self._bg_jobs - 1)
            self._update_bg_menu()

    # ── auto-record callbacks ─────────────────────────────────────────────────

    def _auto_start(self, meeting_name: str, bundle_id: str | None = None):
        if self._recorder and self._recorder.is_recording:
            return
        self._do_start(meeting_name=meeting_name, bundle_id=bundle_id)

    def _auto_stop(self):
        if not (self._recorder and self._recorder.is_recording):
            return
        started_at = self._started_at or datetime.now()
        duration = (datetime.now() - started_at).total_seconds()
        if duration < MIN_SAVE_SECS:
            log.info(
                "Auto-recording discarded (%.0fs < %ds min)",
                duration, MIN_SAVE_SECS,
            )
            audio_path = self._recorder.stop()
            if audio_path:
                audio_path.unlink(missing_ok=True)
            self._recorder = None
            self._started_at = None
            self._restore_idle()
            return
        self._do_stop()

    # ── background transcription ──────────────────────────────────────────────

    def _process(self, audio_path: Path | None, started_at: datetime):
        try:
            self._process_inner(audio_path, started_at)
        finally:
            with self._bg_lock:
                self._bg_jobs = max(0, self._bg_jobs - 1)
            self._update_bg_menu()

    def _process_inner(self, audio_path: Path | None, started_at: datetime):
        if not audio_path:
            log.warning("No audio captured")
            _notify("Trnscrb", "Error", "No audio captured.")
            return

        evt          = get_current_or_upcoming_event()
        meeting_name = evt["title"] if evt else f"meeting-{started_at.strftime('%H%M')}"
        log.info("Processing recording: %s (%s)", meeting_name, audio_path)

        try:
            segments = transcriber.transcribe(audio_path)
            log.info("Transcription complete: %d segments", len(segments))
        except Exception as e:
            log.error("Transcription failed: %s", e, exc_info=True)
            audio_path.unlink(missing_ok=True)
            _notify("Trnscrb", "Transcription failed", str(e))
            return

        hf_token = _read_hf_token()
        if hf_token and segments:
            try:
                diar     = diarizer.diarize(audio_path, hf_token)
                segments = diarizer.merge(segments, diar)
                log.info("Diarization complete")
            except Exception as e:
                log.warning("Diarization failed: %s", e, exc_info=True)

        audio_path.unlink(missing_ok=True)

        text = storage.format_transcript(segments, started_at, meeting_name)
        path = storage.get_transcript_path(meeting_name, started_at)
        storage.save_transcript(path, text)
        log.info("Transcript saved: %s", path)

        _notify("Trnscrb", f"Saved: {meeting_name}", f"~/meeting-notes/{path.name}")

        if get_setting("auto_enrich"):
            log.info("Starting enrichment for %s", path.name)
            try:
                from trnscrb.enricher import enrich_transcript
                result = enrich_transcript(text, calendar_event=evt)
                updated = (
                    result["enriched_transcript"]
                    + "\n\n" + "=" * 60 + "\n\n"
                    + result["enrichment"]
                )
                storage.save_transcript(path, updated)
                log.info("Enrichment complete: %s", path.name)
                _notify("Trnscrb", "Enrichment complete", path.name)
            except Exception as e:
                log.error("Enrichment failed: %s", e, exc_info=True)
                _notify("Trnscrb", "Enrichment failed", str(e))

        if get_setting("auto_integrate"):
            _integrate_notes(
                path,
                get_setting("integrate_prompt"),
                get_setting("integrate_allowed_tools") or "",
            )

    def _restore_idle(self):
        """Return to idle/watching — recording can start immediately."""
        state = "watching" if (self._watcher and self._watcher.is_watching) else "idle"
        self._set_state(state)
        self._update_bg_menu()

    def _update_bg_menu(self):
        """Update the stop item to show background job count when not recording."""
        with self._bg_lock:
            n = self._bg_jobs
        if self._current_state not in ("idle", "watching"):
            return
        if n > 0:
            self._stop_item.title = f"Transcribing {n} meeting{'s' if n > 1 else ''}…"
            self._stop_item.set_callback(None)
        else:
            self._stop_item.title = "Stop Transcribing"
            self._stop_item.set_callback(None)

    # ── state / icon management ───────────────────────────────────────────────

    def _set_state(self, state: str):
        """state: idle | watching | recording"""
        self._current_state = state
        if state in ("idle", "watching"):
            self._start_item.set_callback(self.start_recording)
            self._stop_item.title = "Stop Transcribing"
            self._stop_item.set_callback(None)
        elif state == "recording":
            self._start_item.set_callback(None)
            self._stop_item.title = "Stop Transcribing"
            self._stop_item.set_callback(self.stop_recording)

        self._set_icon_state(state)

    def _set_icon_state(self, state: str):
        rec_icon  = icon_path(recording=True)
        idle_icon = icon_path(recording=False)
        if state == "recording":
            self.icon, self.title = (rec_icon, None) if rec_icon else (None, _EMOJI_RECORDING)
        else:
            self.icon, self.title = (idle_icon, None) if idle_icon else (None, _EMOJI_IDLE)


def _find_claude_cli() -> str | None:
    """Find the claude CLI binary, checking common locations if not on PATH."""
    import shutil

    claude = shutil.which("claude")
    if claude:
        return claude
    for candidate in (
        Path.home() / ".local" / "bin" / "claude",
        Path("/usr/local/bin/claude"),
        Path("/opt/homebrew/bin/claude"),
    ):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def _integrate_notes(
    transcript_path: Path,
    prompt_template: str,
    allowed_tools: str,
) -> None:
    """Fire-and-forget: ask Claude Code to integrate the transcript into notes.

    ``prompt_template`` may contain ``{transcript_path}`` which is substituted
    with the absolute transcript path.  ``allowed_tools`` is a comma-separated
    tool list passed to ``--allowedTools``; empty string omits the flag.
    """
    claude = _find_claude_cli()
    if not claude:
        log.warning("Claude CLI not found in PATH or common locations, skipping note integration")
        return
    try:
        prompt = prompt_template.format(transcript_path=transcript_path)
    except (KeyError, IndexError) as e:
        log.error("Invalid integrate_prompt template (%s); skipping note integration", e)
        return
    cmd = [claude, "-p", prompt]
    if allowed_tools:
        cmd += ["--allowedTools", allowed_tools]
    log.info("Starting note integration via Claude CLI for %s", transcript_path.name)
    try:
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        log.error("Failed to launch Claude CLI for note integration: %s", e)


def _read_hf_token() -> str | None:
    token = os.environ.get("HF_TOKEN")
    if token:
        return token
    token_file = Path.home() / ".cache" / "huggingface" / "token"
    if token_file.exists():
        return token_file.read_text().strip() or None
    return None


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    import AppKit
    app = TrnscrbApp()
    AppKit.NSApplication.sharedApplication().setActivationPolicy_(
        AppKit.NSApplicationActivationPolicyAccessory
    )
    app.run()
