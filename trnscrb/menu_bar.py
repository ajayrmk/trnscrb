"""macOS menu bar app (rumps).

States:
  idle        — mic icon, Start enabled, Stop disabled
  watching    — mic icon (auto-record on, listening)
  recording   — red icon, Start disabled, Stop enabled
  transcribing— red icon, Start disabled, Stop shows "Transcribing…" (disabled)
"""
import os
import subprocess
import threading
from datetime import datetime
from pathlib import Path

import rumps

from trnscrb import recorder as rec_module, transcriber, diarizer, storage
from trnscrb.calendar_integration import get_current_or_upcoming_event
from trnscrb.icon import icon_path, generate_icons
from trnscrb.watcher import MicWatcher
from trnscrb.settings import get as get_setting, put as put_setting

_EMOJI_IDLE      = "🎙"
_EMOJI_RECORDING = "🔴"


class TrnscrbApp(rumps.App):
    def __init__(self):
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
        self._auto_item  = rumps.MenuItem("Auto-transcribe: Off", callback=self.toggle_auto_record)

        self.menu = [
            self._start_item,
            self._stop_item,
            None,
            self._auto_item,
            None,
            rumps.MenuItem("Open Notes Folder", callback=self.open_folder),
            None,
            rumps.MenuItem("Quit", callback=self.quit_app),
        ]

        self._recorder:   rec_module.Recorder | None = None
        self._started_at: datetime | None = None
        self._watcher:    MicWatcher | None = None

        self._set_state("idle")

        if get_setting("auto_record"):
            self._start_watcher()
            self._auto_item.title = "Auto-transcribe: On ✓"

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
            rumps.notification("Trnscrb", "Auto-transcribe off", "")
        else:
            self._start_watcher()
            sender.title = "Auto-transcribe: On ✓"
            put_setting("auto_record", True)
            rumps.notification("Trnscrb", "Auto-transcribe on",
                               "Will start when mic is active for 5+ seconds")

    def open_folder(self, _):
        subprocess.run(["open", str(storage.ensure_notes_dir())])

    def quit_app(self, _):
        if self._watcher:
            self._watcher.stop()
        if self._recorder and self._recorder.is_recording:
            self._recorder.stop()
        rumps.quit_application()

    # ── shared start / stop ───────────────────────────────────────────────────

    def _do_start(self, meeting_name: str = ""):
        if not meeting_name:
            evt = get_current_or_upcoming_event()
            meeting_name = evt["title"] if evt else ""

        device = rec_module.Recorder.find_blackhole_device()
        self._recorder   = rec_module.Recorder(device=device)
        self._started_at = datetime.now()
        self._recorder.start()
        self._set_state("recording")

        source = "BlackHole (system + mic)" if device is not None else "built-in mic"
        label  = f" — {meeting_name}" if meeting_name else ""
        rumps.notification("Trnscrb", f"Transcription started{label}", f"via {source}")

    def _do_stop(self):
        started_at     = self._started_at or datetime.now()
        recorder       = self._recorder
        self._recorder = None
        self._set_state("transcribing")

        threading.Thread(
            target=self._process, args=(recorder, started_at), daemon=True
        ).start()

    # ── auto-record callbacks ─────────────────────────────────────────────────

    def _auto_start(self, meeting_name: str):
        if getattr(self, "_current_state", "idle") in ("recording", "transcribing"):
            return
        self._do_start(meeting_name=meeting_name)

    def _auto_stop(self):
        if self._recorder and self._recorder.is_recording:
            self._do_stop()

    # ── background transcription ──────────────────────────────────────────────

    def _process(self, recorder: rec_module.Recorder, started_at: datetime):
        audio_path = recorder.stop()
        if not audio_path:
            self._restore_idle()
            rumps.notification("Trnscrb", "Error", "No audio captured.")
            return

        evt          = get_current_or_upcoming_event()
        meeting_name = evt["title"] if evt else f"meeting-{started_at.strftime('%H%M')}"

        try:
            segments = transcriber.transcribe(audio_path)
        except Exception as e:
            audio_path.unlink(missing_ok=True)
            self._restore_idle()
            rumps.notification("Trnscrb", "Transcription failed", str(e))
            return

        hf_token = _read_hf_token()
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

        self._restore_idle()
        rumps.notification("Trnscrb", f"Saved: {meeting_name}", f"~/meeting-notes/{path.name}")

    def _restore_idle(self):
        """Called from background thread when transcription finishes."""
        state = "watching" if (self._watcher and self._watcher.is_watching) else "idle"
        self._set_state(state)

    # ── state / icon management ───────────────────────────────────────────────

    def _set_state(self, state: str):
        """state: idle | watching | recording | transcribing"""
        self._current_state = state
        if state in ("idle", "watching"):
            self._start_item.set_callback(self.start_recording)
            self._stop_item.title = "Stop Transcribing"
            self._stop_item.set_callback(None)
        elif state == "recording":
            self._start_item.set_callback(None)
            self._stop_item.title = "Stop Transcribing"
            self._stop_item.set_callback(self.stop_recording)
        elif state == "transcribing":
            self._start_item.set_callback(None)
            self._stop_item.title = "Transcribing…"
            self._stop_item.set_callback(None)

        self._set_icon_state(state)

    def _set_icon_state(self, state: str):
        rec_icon  = icon_path(recording=True)
        idle_icon = icon_path(recording=False)
        if state in ("recording", "transcribing"):
            self.icon, self.title = (rec_icon, None) if rec_icon else (None, _EMOJI_RECORDING)
        else:
            self.icon, self.title = (idle_icon, None) if idle_icon else (None, _EMOJI_IDLE)


def _read_hf_token() -> str | None:
    token = os.environ.get("HF_TOKEN")
    if token:
        return token
    token_file = Path.home() / ".cache" / "huggingface" / "token"
    if token_file.exists():
        return token_file.read_text().strip() or None
    return None


def main():
    import AppKit
    app = TrnscrbApp()
    AppKit.NSApplication.sharedApplication().setActivationPolicy_(
        AppKit.NSApplicationActivationPolicyAccessory
    )
    app.run()
