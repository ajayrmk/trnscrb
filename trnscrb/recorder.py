"""Audio capture using sounddevice.

Supports mic-only or BlackHole 2ch (system audio) as input.
Records at 16 kHz mono — the sample rate Whisper expects.
"""
import threading
import tempfile
from pathlib import Path

import numpy as np
import sounddevice as sd
import scipy.io.wavfile as wavfile

SAMPLE_RATE = 16_000  # Whisper expects 16 kHz


class Recorder:
    def __init__(self, device: int | str | None = None):
        # device=None → system default input
        self.device = device
        self._recording = False
        self._frames: list[np.ndarray] = []
        self._stream: sd.InputStream | None = None
        self._lock = threading.Lock()

    # ── public ──────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._frames = []
        self._recording = True
        self._stream = sd.InputStream(
            device=self.device,
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            callback=self._callback,
            blocksize=1024,
        )
        self._stream.start()

    def stop(self) -> Path | None:
        """Stop recording and return the path to a temporary WAV file."""
        self._recording = False
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None

        with self._lock:
            frames = list(self._frames)

        if not frames:
            return None

        audio = np.concatenate(frames, axis=0).flatten()
        audio_int16 = (audio * 32_767).astype(np.int16)

        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        wavfile.write(tmp.name, SAMPLE_RATE, audio_int16)
        return Path(tmp.name)

    @property
    def is_recording(self) -> bool:
        return self._recording

    # ── helpers ─────────────────────────────────────────────────────────────

    def _callback(self, indata, frames, time_info, status):
        if self._recording:
            with self._lock:
                self._frames.append(indata.copy())

    # ── class-level utilities ────────────────────────────────────────────────

    @staticmethod
    def find_blackhole_device() -> int | None:
        for i, dev in enumerate(sd.query_devices()):
            if "BlackHole" in dev["name"] and dev["max_input_channels"] > 0:
                return i
        return None

    @staticmethod
    def list_input_devices() -> list[dict]:
        return [
            {"index": i, "name": dev["name"], "channels": dev["max_input_channels"]}
            for i, dev in enumerate(sd.query_devices())
            if dev["max_input_channels"] > 0
        ]
