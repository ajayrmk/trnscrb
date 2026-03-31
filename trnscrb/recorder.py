"""Audio capture: mic via sounddevice + meeting app audio via ScreenCaptureKit.

When app_bundle_id is provided, captures two streams in parallel:
  - SCK: remote participants' audio (device-independent)
  - Mic: user's voice via sounddevice (follows macOS default input)

When app_bundle_id is None, captures mic only (manual recording mode).

Audio is written to temp files on disk during recording to keep memory
usage constant regardless of meeting length.
"""
import logging
import threading
import tempfile
from pathlib import Path

import numpy as np
import sounddevice as sd
import scipy.io.wavfile as wavfile

from trnscrb.sck import SCKCapture, find_binary, SAMPLE_RATE as SCK_RATE
from trnscrb.screen_capture import check_permission

SAMPLE_RATE = 16_000  # Whisper expects 16 kHz

log = logging.getLogger(__name__)


class Recorder:
    def __init__(self, app_bundle_id: str | None = None):
        self._app_bundle_id = app_bundle_id
        self._recording = False
        self._stream: sd.InputStream | None = None
        self._sck: SCKCapture | None = None
        self._lock = threading.Lock()
        self._device_error = False
        # Temp files for streaming audio to disk
        self._mic_file = None
        self._mic_samples = 0

    # ── public ──────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._recording = True
        self._mic_samples = 0

        # Create temp file for mic audio (raw float32 PCM)
        self._mic_file = tempfile.NamedTemporaryFile(
            suffix=".pcm", delete=False, prefix="trnscrb_mic_"
        )

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

        # Close mic temp file
        mic_path = None
        if self._mic_file:
            mic_path = Path(self._mic_file.name)
            self._mic_file.close()
            self._mic_file = None

        # Collect SCK audio (also writes to temp file)
        sck_path = None
        if self._sck:
            sck_path = self._sck.stop()
            self._sck = None

        # Read back audio from temp files
        mic_audio = np.array([], dtype=np.float32)
        if mic_path and mic_path.stat().st_size > 0:
            mic_audio = np.fromfile(str(mic_path), dtype=np.float32)
            mic_path.unlink(missing_ok=True)
        elif mic_path:
            mic_path.unlink(missing_ok=True)

        sck_audio = np.array([], dtype=np.float32)
        if sck_path and sck_path.stat().st_size > 0:
            sck_audio = np.fromfile(str(sck_path), dtype=np.float32)
            sck_path.unlink(missing_ok=True)
        elif sck_path:
            sck_path.unlink(missing_ok=True)

        if len(mic_audio) == 0 and len(sck_audio) == 0:
            return None

        # Mix if both sources present
        if len(mic_audio) > 0 and len(sck_audio) > 0:
            # Zero-pad the shorter stream
            max_len = max(len(mic_audio), len(sck_audio))
            if len(mic_audio) < max_len:
                mic_audio = np.pad(mic_audio, (0, max_len - len(mic_audio)))
            if len(sck_audio) < max_len:
                sck_audio = np.pad(sck_audio, (0, max_len - len(sck_audio)))

            # Normalize both streams to similar RMS so Whisper hears both voices
            mic_rms = np.sqrt(np.mean(mic_audio**2))
            sck_rms = np.sqrt(np.mean(sck_audio**2))
            if mic_rms > 1e-6 and sck_rms > 1e-6:
                gain = sck_rms / mic_rms
                gain = min(gain, 5.0)
                mic_audio = mic_audio * gain

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
        if status:
            log.warning("sounddevice status: %s", status)
            if not self._device_error:
                self._device_error = True
                threading.Thread(target=self._restart_mic_stream, daemon=True).start()
            return
        if self._recording and self._mic_file:
            data = indata.copy().flatten()
            with self._lock:
                self._mic_file.write(data.tobytes())
                self._mic_samples += len(data)

    def _restart_mic_stream(self) -> None:
        """Restart mic stream after a device change (BLE connect/disconnect)."""
        try:
            if self._stream:
                self._stream.stop()
                self._stream.close()
            self._stream = sd.InputStream(
                device=None,
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="float32",
                callback=self._callback,
                blocksize=1024,
            )
            self._stream.start()
            self._device_error = False
            log.info("Mic stream restarted after device change")
        except Exception as e:
            log.error("Failed to restart mic stream: %s", e)
