"""Python wrapper for the sck-capture Swift helper.

Manages the subprocess lifecycle and collects raw float32 PCM frames
in the same format as sounddevice callbacks.
"""
import subprocess
import threading
from pathlib import Path

import numpy as np

# Default binary location (built by trnscrb install)
_BINARY_NAME = "sck-capture"
_INSTALL_DIR = Path.home() / ".local" / "share" / "trnscrb"
_BUILD_DIR = Path(__file__).resolve().parent.parent / "swift" / "sck-capture" / ".build" / "debug"

SAMPLE_RATE = 16_000
CHANNELS = 1
BYTES_PER_SAMPLE = 4  # float32
CHUNK_SAMPLES = 1024
CHUNK_BYTES = CHUNK_SAMPLES * BYTES_PER_SAMPLE


def find_binary() -> Path | None:
    """Locate the sck-capture binary."""
    for directory in [_INSTALL_DIR, _BUILD_DIR]:
        path = directory / _BINARY_NAME
        if path.exists():
            return path
    return None


class SCKCapture:
    """Capture audio from a macOS app via ScreenCaptureKit."""

    def __init__(self, bundle_id: str):
        self._bundle_id = bundle_id
        self._process: subprocess.Popen | None = None
        self._frames: list[np.ndarray] = []
        self._lock = threading.Lock()
        self._reader_thread: threading.Thread | None = None

    def start(self) -> None:
        binary = find_binary()
        if binary is None:
            raise RuntimeError(
                "sck-capture binary not found. Run: cd swift/sck-capture && swift build"
            )

        self._frames = []
        self._process = subprocess.Popen(
            [str(binary), self._bundle_id],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        # Wait for READY on stderr (up to 5 seconds)
        ready = False
        for _ in range(50):
            line = self._process.stderr.readline().decode().strip()
            if line == "READY":
                ready = True
                break
            if line.startswith("ERROR") or line.startswith("FATAL"):
                self._process.terminate()
                raise RuntimeError(f"sck-capture failed: {line}")

        if not ready:
            self._process.terminate()
            raise RuntimeError("sck-capture did not become ready in time")

        # Start reader thread
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()

    def stop(self) -> list[np.ndarray]:
        """Stop capture and return collected frames."""
        if self._process and self._process.poll() is None:
            self._process.terminate()
            self._process.wait(timeout=3)

        if self._reader_thread:
            self._reader_thread.join(timeout=2)

        with self._lock:
            frames = list(self._frames)
        return frames

    def _read_loop(self) -> None:
        """Read PCM chunks from subprocess stdout."""
        stdout = self._process.stdout
        while True:
            data = stdout.read(CHUNK_BYTES)
            if not data:
                break
            arr = np.frombuffer(data, dtype=np.float32)
            with self._lock:
                self._frames.append(arr)
