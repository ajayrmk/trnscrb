"""Python wrapper for the sck-capture Swift helper.

Manages the subprocess lifecycle and streams raw float32 PCM to a temp
file on disk (constant memory regardless of recording duration).
"""
import subprocess
import tempfile
import threading
from pathlib import Path

# Default binary location (built by trnscrb install)
_BINARY_NAME = "sck-capture"
_INSTALL_DIR = Path.home() / ".local" / "share" / "trnscrb"
# Dev fallbacks: bundled package source or repo root swift/ dir
_PKG_BUILD_DIR = Path(__file__).resolve().parent / "sck-capture" / ".build" / "debug"
_REPO_BUILD_DIR = Path(__file__).resolve().parent.parent / "swift" / "sck-capture" / ".build" / "debug"

SAMPLE_RATE = 16_000
CHANNELS = 1
BYTES_PER_SAMPLE = 4  # float32
CHUNK_SAMPLES = 1024
CHUNK_BYTES = CHUNK_SAMPLES * BYTES_PER_SAMPLE


def find_binary() -> Path | None:
    """Locate the sck-capture binary."""
    for directory in [_INSTALL_DIR, _PKG_BUILD_DIR, _REPO_BUILD_DIR]:
        path = directory / _BINARY_NAME
        if path.exists():
            return path
    return None


class SCKCapture:
    """Capture audio from a macOS app via ScreenCaptureKit."""

    def __init__(self, bundle_id: str):
        self._bundle_id = bundle_id
        self._process: subprocess.Popen | None = None
        self._reader_thread: threading.Thread | None = None
        self._file = None

    def start(self) -> None:
        binary = find_binary()
        if binary is None:
            raise RuntimeError(
                "sck-capture binary not found. Run: trnscrb install"
            )

        self._file = tempfile.NamedTemporaryFile(
            suffix=".pcm", delete=False, prefix="trnscrb_sck_"
        )

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
                self._file.close()
                Path(self._file.name).unlink(missing_ok=True)
                raise RuntimeError(f"sck-capture failed: {line}")

        if not ready:
            self._process.terminate()
            self._file.close()
            Path(self._file.name).unlink(missing_ok=True)
            raise RuntimeError("sck-capture did not become ready in time")

        # Start reader thread
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()

    def stop(self) -> Path | None:
        """Stop capture and return path to temp PCM file."""
        if self._process and self._process.poll() is None:
            self._process.terminate()
            self._process.wait(timeout=3)

        if self._reader_thread:
            self._reader_thread.join(timeout=2)

        if self._file:
            path = Path(self._file.name)
            self._file.close()
            self._file = None
            return path
        return None

    def _read_loop(self) -> None:
        """Read PCM chunks from subprocess stdout and write to temp file."""
        stdout = self._process.stdout
        while True:
            data = stdout.read(CHUNK_BYTES)
            if not data:
                break
            self._file.write(data)
