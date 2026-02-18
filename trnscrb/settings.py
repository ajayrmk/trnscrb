"""Persistent user settings stored in ~/.config/trnscrb/settings.json."""
import json
from pathlib import Path

_SETTINGS_FILE = Path.home() / ".config" / "trnscrb" / "settings.json"

_DEFAULTS: dict = {
    "auto_record": True,    # start watching for mic activity on launch
    "model_size": "small",  # whisper model
}


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
