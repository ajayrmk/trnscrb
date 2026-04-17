"""Persistent user settings stored in ~/.config/trnscrb/settings.json."""
import json
from pathlib import Path

_SETTINGS_FILE = Path.home() / ".config" / "trnscrb" / "settings.json"

_DEFAULT_INTEGRATE_PROMPT = (
    "/organize-notes Read the meeting transcript at {transcript_path} "
    "and integrate the key information into the notes."
)

_DEFAULTS: dict = {
    "auto_record": True,    # start watching for mic activity on launch
    "model_size": "small",  # whisper model
    "auto_enrich": False,   # enrich transcripts with Claude after saving
    "auto_integrate": True,  # integrate transcripts into notes via Claude Code
    # Claude CLI prompt for note integration. "{transcript_path}" is replaced
    # with the absolute path of the saved transcript.
    "integrate_prompt": _DEFAULT_INTEGRATE_PROMPT,
    # Comma-separated tools passed to `claude -p --allowedTools` when
    # integrating notes.  Empty string omits the flag (all tools allowed).
    "integrate_allowed_tools": "Read,Write,Edit,Glob,Grep",
}


def settings_file() -> Path:
    """Ensure the settings file exists with all default keys, return its path.

    Any keys missing from the on-disk file are filled in from defaults so the
    user can see and customise every option when opening the file.
    """
    on_disk: dict = {}
    if _SETTINGS_FILE.exists():
        try:
            on_disk = json.loads(_SETTINGS_FILE.read_text())
        except Exception:
            on_disk = {}
    merged = {**_DEFAULTS, **on_disk}
    if merged != on_disk:
        save(merged)
    return _SETTINGS_FILE


def load() -> dict:
    if _SETTINGS_FILE.exists():
        try:
            return {**_DEFAULTS, **json.loads(_SETTINGS_FILE.read_text())}
        except Exception:
            pass
    return dict(_DEFAULTS)


def save(settings: dict) -> None:
    _SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _SETTINGS_FILE.write_text(json.dumps(settings, indent=2))


def get(key: str):
    return load().get(key, _DEFAULTS.get(key))


def put(key: str, value) -> None:
    s = load()
    s[key] = value
    save(s)
