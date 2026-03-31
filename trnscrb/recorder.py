"""Audio capture: mic via sounddevice + meeting app audio via ScreenCaptureKit.

When app_bundle_id is provided, captures two streams in parallel:
  - SCK: remote participants' audio (device-independent)
  - Mic: user's voice via sounddevice (follows macOS default input)

When app_bundle_id is None, captures mic only (manual recording mode).
"""
import threading
import tempfile
from pathlib import Path

import numpy as np
import sounddevice as sd
import scipy.io.wavfile as wavfile

from trnscrb.sck import SCKCapture, find_binary
from trnscrb.screen_capture import check_permission

SAMPLE_RATE = 16_000  # Whisper expects 16 kHz


class Recorder:
    def __init__(self, app_bundle_id: str | None = None):
        self._app_bundle_id = app_bundle_id
        self._recording = False
        self._mic_frames: list[np.ndarray] = []
        self._stream: sd.InputStream | None = None
        self._sck: SCKCapture | None = None
        self._lock = threading.Lock()

    # ── public ──────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._mic_frames = []
        self._recording = True

        # Start mic capture
        self._stream = sd.InputStream(
            device=None,
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            callback=self._callback,
            blocksize=1024,
        )
        self._stream.start()

        # Start SCK capture if we have a target app
        if self._app_bundle_id and find_binary() and check_permission():
            try:
                self._sck = SCKCapture(self._app_bundle_id)
                self._sck.start()
            except Exception:
                self._sck = None  # Fall back to mic only

    def stop(self) -> Path | None:
        """Stop recording and return the path to a temporary WAV file."""
        self._recording = False

        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None

        # Collect mic frames
        with self._lock:
            mic_frames = list(self._mic_frames)

        # Collect SCK frames
        sck_frames = []
        if self._sck:
            sck_frames = self._sck.stop()
            self._sck = None

        if not mic_frames and not sck_frames:
            return None

        # Build mic audio
        mic_audio = np.concatenate(mic_frames, axis=0).flatten() if mic_frames else np.array([], dtype=np.float32)

        # Build SCK audio
        sck_audio = np.concatenate(sck_frames) if sck_frames else np.array([], dtype=np.float32)

        # Mix if both sources present
        if len(mic_audio) > 0 and len(sck_audio) > 0:
            # Zero-pad the shorter stream
            max_len = max(len(mic_audio), len(sck_audio))
            if len(mic_audio) < max_len:
                mic_audio = np.pad(mic_audio, (0, max_len - len(mic_audio)))
            if len(sck_audio) < max_len:
                sck_audio = np.pad(sck_audio, (0, max_len - len(sck_audio)))
            audio = np.clip(mic_audio + sck_audio, -1.0, 1.0)
        elif len(sck_audio) > 0:
            audio = sck_audio
        else:
            audio = mic_audio

        if len(audio) == 0:
            return None

        audio_int16 = (audio * 32_767).astype(np.int16)
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        wavfile.write(tmp.name, SAMPLE_RATE, audio_int16)
        return Path(tmp.name)

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def audio_source_description(self) -> str:
        if self._sck:
            return "ScreenCaptureKit (system + mic)"
        return "mic"

    # ── internal ────────────────────────────────────────────────────────────

    def _callback(self, indata, frames, time_info, status):
        if self._recording:
            with self._lock:
                self._mic_frames.append(indata.copy())
