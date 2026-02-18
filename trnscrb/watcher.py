"""Automatic meeting detector and recording trigger.

Uses CoreAudio's kAudioDevicePropertyDeviceIsRunningSomewhere to detect
microphone activity — the same signal that lights up the orange menu-bar dot.

Two stop conditions (whichever comes first):
  1. Mic goes idle for GRACE_SECS (normal call end)
  2. No meeting app/tab detected for APP_GONE_POLLS consecutive app-checks,
     even if mic is still technically active (handles Chrome keeping mic warm
     after leaving Google Meet).  App is checked every APP_POLL_EVERY mic
     polls so slow osascript doesn't block the mic-poll loop.

State machine:
  idle  ──(mic on 5s)──► warming ──(5s elapsed)──► recording
                             │                          │
                         (mic off)          (mic off OR meeting gone)
                             │                          │
                             ▼                          ▼
                           idle                      cooling
                                                        │
                                                 (5s elapsed)
                                                        │
                                                  stop + save
"""
import ctypes
import os
import subprocess
import threading
import time
from datetime import datetime
from typing import Callable

# ── Timing thresholds ─────────────────────────────────────────────────────────
WARMUP_SECS    = 5    # mic must be active this long before we start
GRACE_SECS     = 5    # mic must be idle this long before we stop
MIN_SAVE_SECS  = 30   # recordings shorter than this are discarded
POLL_SECS      = 1.0  # how often we check mic (fast CoreAudio call)
APP_POLL_EVERY = 4    # run the slow meeting-app check every N mic polls (~4s)
APP_GONE_POLLS = 3    # N consecutive app-gone checks → start cooling (~12s)

# ── Meeting app detection ─────────────────────────────────────────────────────
# Used by detect_meeting() at recording START — can be broad because the mic
# activity signal already confirms something real is happening.
_NATIVE_APPS = [
    ("zoom.us",                 "Zoom"),
    ("Slack Helper",            "Slack Huddle"),
    ("Microsoft Teams Helper",  "Microsoft Teams"),
    ("Webex",                   "Webex"),
    ("Around Helper",           "Around"),
    ("Tuple",                   "Tuple"),
    ("Loom",                    "Loom"),
    ("FaceTime",                "FaceTime"),
    ("Discord Helper",          "Discord"),
]

# Used by is_meeting_app_running() during STOP detection — must be NARROW.
# "Slack Helper", "Teams Helper", "Discord Helper" etc. are ALWAYS present
# when those apps are open, even when NOT in a meeting → false positives.
# Only list processes that exist exclusively during an active session.
_ACTIVE_SESSION_PROCS = [
    "CptHost",   # Zoom: meeting capture host — only present during an active Zoom call
    "FaceTime",  # FaceTime — only runs during an active call
    "Tuple",     # Tuple — only runs during an active screen-share session
]

# CoreAudio process-level constants (macOS 14+)
# Powers the orange privacy indicator — lets us see which PID is using mic input.
_kProcessObjectList    = 0x706C7374  # 'plst'
_kProcessPID           = 0x70706964  # 'ppid'
_kProcessIsRunningIn   = 0x70697220  # 'pir ' — is this process using audio input?

# ── CoreAudio constants ───────────────────────────────────────────────────────
_kSysObject          = 1
_kDefaultInputDevice = 0x64496E20   # 'dIn '
_kScopeGlobal        = 0x676C6F62   # 'glob'
_kElementMain        = 0
_kIsRunningSomewhere = 0x676F6E65   # 'gone' (kAudioDevicePropertyDeviceIsRunningSomewhere)


class MicWatcher:
    """
    Polls CoreAudio every POLL_SECS seconds and fires:
      on_start(meeting_name: str)  — when a meeting is confirmed to have started
      on_stop()                    — when the meeting has ended
    """

    def __init__(
        self,
        on_start: Callable[[str], None],
        on_stop:  Callable[[], None],
    ):
        self.on_start = on_start
        self.on_stop  = on_stop

        self._thread: threading.Thread | None = None
        self._running  = False
        self._state    = "idle"   # idle | warming | recording | cooling
        self._since:   datetime | None = None
        self._rec_started: datetime | None = None
        self._no_app_polls = 0    # consecutive polls without a meeting app

    def start(self) -> None:
        if self._running:
            return
        self._running      = True
        self._state        = "idle"
        self._since        = None
        self._no_app_polls = 0
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False

    @property
    def is_watching(self) -> bool:
        return self._running

    @property
    def state(self) -> str:
        return self._state

    # ── event loop ────────────────────────────────────────────────────────────

    def _loop(self) -> None:
        # Separate the fast mic check (every POLL_SECS) from the slow app check
        # (osascript can take 3-4 s — running it every poll would block the loop).
        _app_counter = 0   # counts mic polls; app is checked every APP_POLL_EVERY

        while self._running:
            active  = is_mic_in_use()
            now     = datetime.now()
            elapsed = (now - self._since).total_seconds() if self._since else 0

            if self._state == "idle":
                if active:
                    self._state        = "warming"
                    self._since        = now
                    self._no_app_polls = 0

            elif self._state == "warming":
                if not active:
                    # False positive — Siri, dictation, brief mic check
                    self._state = "idle"
                    self._since = None
                elif elapsed >= WARMUP_SECS:
                    self._rec_started  = now
                    self._state        = "recording"
                    self._since        = now
                    self._no_app_polls = 0
                    _app_counter       = APP_POLL_EVERY  # check app on first recording poll
                    meeting_name       = detect_meeting()
                    self.on_start(meeting_name)

            elif self._state == "recording":
                if not active:
                    # Mic went silent — start grace period immediately
                    self._state        = "cooling"
                    self._since        = now
                    self._no_app_polls = 0
                else:
                    # Mic still active — periodically check if the meeting app is
                    # still open.  Chrome keeps mic "warm" after leaving Meet, so
                    # we need this secondary signal.
                    _app_counter += 1
                    if _app_counter >= APP_POLL_EVERY:
                        _app_counter = 0
                        if is_meeting_app_running():
                            self._no_app_polls = 0
                        else:
                            self._no_app_polls += 1
                            if self._no_app_polls >= APP_GONE_POLLS:
                                # Meeting app gone — treat as call ended
                                self._state        = "cooling"
                                self._since        = now
                                self._no_app_polls = 0

            elif self._state == "cooling":
                if active and is_meeting_app_running():
                    # Meeting came back (e.g. rejoined)
                    self._state        = "recording"
                    self._since        = now
                    self._no_app_polls = 0
                    _app_counter       = APP_POLL_EVERY
                elif elapsed >= GRACE_SECS:
                    duration = (
                        (now - self._rec_started).total_seconds()
                        if self._rec_started else 0
                    )
                    self._state        = "idle"
                    self._since        = None
                    self._rec_started  = None
                    self._no_app_polls = 0
                    if duration >= MIN_SAVE_SECS:
                        self.on_stop()

            time.sleep(POLL_SECS)


# ── CoreAudio mic detection ────────────────────────────────────────────────────

class _PropAddr(ctypes.Structure):
    _fields_ = [
        ("mSelector", ctypes.c_uint32),
        ("mScope",    ctypes.c_uint32),
        ("mElement",  ctypes.c_uint32),
    ]


def is_mic_in_use() -> bool:
    """True if ANY process is currently using the default audio input device."""
    try:
        ca = ctypes.CDLL(
            "/System/Library/Frameworks/CoreAudio.framework/CoreAudio"
        )
        addr = _PropAddr(_kDefaultInputDevice, _kScopeGlobal, _kElementMain)
        dev  = ctypes.c_uint32(0)
        sz   = ctypes.c_uint32(ctypes.sizeof(dev))
        ca.AudioObjectGetPropertyData(
            _kSysObject, ctypes.byref(addr),
            0, None, ctypes.byref(sz), ctypes.byref(dev),
        )
        if dev.value == 0:
            return False

        addr2   = _PropAddr(_kIsRunningSomewhere, _kScopeGlobal, _kElementMain)
        running = ctypes.c_uint32(0)
        sz2     = ctypes.c_uint32(ctypes.sizeof(running))
        status  = ca.AudioObjectGetPropertyData(
            dev.value, ctypes.byref(addr2),
            0, None, ctypes.byref(sz2), ctypes.byref(running),
        )
        return status == 0 and bool(running.value)
    except Exception:
        return False


# ── Meeting presence checks ───────────────────────────────────────────────────

def _meeting_app_pids() -> set[int]:
    """Return PIDs of known native meeting apps that are currently running."""
    pids: set[int] = set()
    try:
        ps = subprocess.run(
            ["ps", "-ax", "-o", "pid=,comm="],
            capture_output=True, text=True, timeout=3,
        )
        for line in ps.stdout.splitlines():
            parts = line.split(None, 1)
            if len(parts) == 2:
                pid_str, comm = parts
                for frag in _ACTIVE_SESSION_PROCS:
                    if frag in comm:
                        try:
                            pids.add(int(pid_str))
                        except ValueError:
                            pass
    except Exception:
        pass
    return pids


def _pids_using_mic_input() -> set[int]:
    """
    Return PIDs of all processes currently capturing audio input.

    Uses CoreAudio's kAudioHardwarePropertyProcessObjectList API (macOS 14+),
    the same mechanism that drives the orange privacy-indicator dot.
    Returns an empty set on older macOS or on any error.
    """
    pids: set[int] = set()
    try:
        ca = ctypes.CDLL(
            "/System/Library/Frameworks/CoreAudio.framework/CoreAudio"
        )
        # 1. How many process objects are there?
        addr = _PropAddr(_kProcessObjectList, _kScopeGlobal, _kElementMain)
        sz = ctypes.c_uint32(0)
        if ca.AudioObjectGetPropertyDataSize(
            _kSysObject, ctypes.byref(addr), 0, None, ctypes.byref(sz)
        ) != 0 or sz.value == 0:
            return pids

        n = sz.value // ctypes.sizeof(ctypes.c_uint32)
        objs = (ctypes.c_uint32 * n)()
        if ca.AudioObjectGetPropertyData(
            _kSysObject, ctypes.byref(addr), 0, None,
            ctypes.byref(sz), ctypes.byref(objs)
        ) != 0:
            return pids

        own_pid = os.getpid()
        for obj_id in objs:
            # Is this process using audio input?
            addr_in = _PropAddr(_kProcessIsRunningIn, _kScopeGlobal, _kElementMain)
            running = ctypes.c_uint32(0)
            sz_r = ctypes.c_uint32(ctypes.sizeof(running))
            if ca.AudioObjectGetPropertyData(
                obj_id, ctypes.byref(addr_in), 0, None,
                ctypes.byref(sz_r), ctypes.byref(running)
            ) != 0 or not running.value:
                continue
            # Get the PID
            addr_pid = _PropAddr(_kProcessPID, _kScopeGlobal, _kElementMain)
            pid = ctypes.c_int32(0)
            sz_p = ctypes.c_uint32(ctypes.sizeof(pid))
            if ca.AudioObjectGetPropertyData(
                obj_id, ctypes.byref(addr_pid), 0, None,
                ctypes.byref(sz_p), ctypes.byref(pid)
            ) == 0 and pid.value != own_pid:
                pids.add(pid.value)
    except Exception:
        pass
    return pids


def is_meeting_app_running() -> bool:
    """
    Accurate check: is an active meeting session in progress right now?

    Strategy (in order):
    1. CoreAudio per-process mic check — if any known meeting-app PID is
       capturing audio input, the meeting is still live.
    2. Active-session process check (CptHost for Zoom, etc.) via ps.
    3. Browser tab URL + title check (handles Google Meet / Teams in browser).
    """
    # 1. Per-process mic check (macOS 14+)
    mic_pids = _pids_using_mic_input()
    if mic_pids:
        meeting_pids = _meeting_app_pids()
        # If any process using the mic belongs to a meeting app → still in meeting
        if mic_pids & meeting_pids:
            return True
        # If mic_pids is non-empty but no meeting app PID matches,
        # fall through — browser-based meeting processes may not be in
        # _ACTIVE_SESSION_PROCS, so we still check the browser tabs.

    # 2. Active-session native process check (narrow list — no helper false-positives)
    try:
        ps = subprocess.run(
            ["ps", "-ax", "-o", "comm="],
            capture_output=True, text=True, timeout=3,
        )
        for frag in _ACTIVE_SESSION_PROCS:
            if frag in ps.stdout:
                return True
    except Exception:
        pass

    # 3. Browser tab URL + title check
    return _browser_has_meeting_tab()


def detect_meeting() -> str:
    """Best-effort: identify which meeting app is active when recording starts."""
    try:
        ps = subprocess.run(["ps", "aux"], capture_output=True, text=True, timeout=3)
        for fragment, name in _NATIVE_APPS:
            if fragment in ps.stdout:
                return name
    except Exception:
        pass

    browser_name = _browser_has_meeting_tab(return_name=True)
    if browser_name:
        return browser_name

    try:
        from trnscrb.calendar_integration import get_current_or_upcoming_event
        evt = get_current_or_upcoming_event()
        if evt and evt.get("title"):
            return evt["title"]
    except Exception:
        pass

    return f"meeting-{datetime.now().strftime('%H%M')}"


_MEET_URLS = [
    "meet.google.com",
    "teams.microsoft.com/meet",
    "teams.microsoft.com/v2",
    "app.huddle.team",
    "zoom.us/j/",
]

_CHROME_TAB_SCRIPT = """
tell application "System Events"
    if not (exists process "Google Chrome") then return ""
end tell
tell application "Google Chrome"
    repeat with w in windows
        repeat with t in tabs of w
            set u to URL of t
            if u contains "meet.google.com" then
                -- "Meeting ended" page: title contains "ended" — don't count it
                if not (title of t contains "ended") then return "Google Meet"
            end if
            if u contains "teams.microsoft.com" then return "Microsoft Teams"
            if u contains "app.huddle.team" then return "Huddle"
            if u contains "zoom.us/j/" then return "Zoom"
        end repeat
    end repeat
end tell
return ""
"""

_SAFARI_TAB_SCRIPT = """
tell application "System Events"
    if not (exists process "Safari") then return ""
end tell
tell application "Safari"
    repeat with w in windows
        try
            set u to URL of current tab of w
            if u contains "meet.google.com" then return "Google Meet"
            if u contains "teams.microsoft.com" then return "Microsoft Teams"
        end try
    end repeat
end tell
return ""
"""


def _browser_has_meeting_tab(return_name: bool = False):
    """
    Check Chrome and Safari for open meeting tabs.
    return_name=False → returns bool (fast presence check)
    return_name=True  → returns str name or None
    """
    for script in [_CHROME_TAB_SCRIPT, _SAFARI_TAB_SCRIPT]:
        try:
            r = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=4,
            )
            name = r.stdout.strip()
            if name:
                return name if return_name else True
        except Exception:
            pass
    return None if return_name else False
