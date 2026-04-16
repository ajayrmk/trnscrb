"""Automatic meeting detector and recording trigger.

Two rules:

* START — the mic has been continuously held by a known meeting source for
  WARMUP_SECS. A *source* is either a native meeting app (Zoom, FaceTime,
  Slack, …) or a recognised browser (Chrome, Safari, Firefox), identified
  by the executable path of the mic-holding PID.  Browser tab/title checks
  are best-effort for naming only — mic hold is sufficient to start.
* STOP  — no *external* process has been using the mic for GRACE_SECS, OR
  (for browser meetings where the tab was verified) the meeting tab has
  disappeared.

Both rules rely on the per-process CoreAudio API (macOS 14+, the same data
source that drives the orange privacy indicator): we can see which PIDs are
capturing audio input and match their executable paths against a small list
of known bundles.

State machine::

    idle ──(mic on)──► warming ──(WARMUP_SECS + meeting source)──► recording
      ▲                   │                                           │
      │               (mic off)                                  (mic off OR
      │                   │                                      browser tab
      │                   ▼                                        gone)
      └──────────────── idle                                          │
                                                                      ▼
                                                                   cooling
                                                                      │
                                                                 (GRACE_SECS)
                                                                      │
                                                                      ▼
                                                                 stop + save
"""
import ctypes
import logging
import os
import subprocess
import threading
import time
from datetime import datetime
from typing import Callable

log = logging.getLogger("trnscrb.watcher")

# ── Child-process tracking ────────────────────────────────────────────────────
# sck-capture (and any other trnscrb subprocess that opens the mic) registers
# its PID here so the stop-detector can ignore it.
_child_pids: set[int] = set()


def register_child_pid(pid: int) -> None:
    _child_pids.add(pid)


def unregister_child_pid(pid: int) -> None:
    _child_pids.discard(pid)


# ── Timing thresholds ─────────────────────────────────────────────────────────
WARMUP_SECS         = 5    # mic must be active this long before we start
GRACE_SECS          = 5    # mic must be idle this long before we stop
MIN_SAVE_SECS       = 30   # recordings shorter than this are discarded by callers
POLL_SECS           = 1.0  # how often we poll CoreAudio
BROWSER_CHECK_EVERY = 4    # while recording a browser meeting, re-check the
                           # tab every N polls (~4s) to catch Chrome/Firefox
                           # keeping the mic warm after the call ends.


# ── Meeting-context fragments ─────────────────────────────────────────────────
# Case-insensitive substrings matched against the full executable paths from
# `ps -ax -o comm=`. The ".app/" anchor ensures we only match real application
# bundles — e.g. avoids matching "SafariPlatformSupport.Helper" on "Safari",
# or "TrialArchivingService" on "Arc".
#
# Native apps: (bundle fragment, display name, bundle ID)
_NATIVE_APPS: list[tuple[str, str, str]] = [
    ("zoom.us.app/",         "Zoom",            "us.zoom.xos"),
    ("Slack.app/",           "Slack Huddle",    "com.tinyspeck.slackmacgap"),
    ("Microsoft Teams.app/", "Microsoft Teams", "com.microsoft.teams2"),
    ("Webex.app/",           "Webex",           "com.webex.meetingmanager"),
    ("Around.app/",          "Around",          "me.around.Around"),
    ("Tuple.app/",           "Tuple",           "app.tuple.app"),
    ("Loom.app/",            "Loom",            "com.loom.desktop"),
    ("FaceTime.app/",        "FaceTime",        "com.apple.FaceTime"),
    ("Discord.app/",         "Discord",         "com.hnc.Discord"),
]

# Only browsers we can actually query for tab/window titles.  Adding a browser
# here requires both a _BROWSER_SCRIPTS entry for naming and a bundle_id the
# Recorder can hand to ScreenCaptureKit.
_BROWSER_BUNDLES: list[str] = [
    "Google Chrome.app/",
    "Safari.app/",
    "Firefox.app/",
]
_BROWSER_BUNDLE_IDS: list[str] = [
    "com.google.Chrome",
    "com.apple.Safari",
    "org.mozilla.firefox",
]


# ── CoreAudio constants ───────────────────────────────────────────────────────
_kSysObject          = 1
_kDefaultInputDevice = 0x64496E20   # 'dIn '
_kScopeGlobal        = 0x676C6F62   # 'glob'
_kElementMain        = 0
_kIsRunningSomewhere = 0x676F6E65   # 'gone' (kAudioDevicePropertyDeviceIsRunningSomewhere)

# Per-process CoreAudio API (macOS 14+) — the same data source that drives
# the orange privacy-indicator dot in the menu bar.
_kProcessObjectList  = 0x70727323   # 'prs#'  kAudioHardwarePropertyProcessObjectList
_kProcessPID         = 0x70706964   # 'ppid'  kAudioProcessPropertyPID
_kProcessIsRunningIn = 0x70697269   # 'piri'  kAudioProcessPropertyIsRunningInput


class _PropAddr(ctypes.Structure):
    _fields_ = [
        ("mSelector", ctypes.c_uint32),
        ("mScope",    ctypes.c_uint32),
        ("mElement",  ctypes.c_uint32),
    ]


# ── MicWatcher ────────────────────────────────────────────────────────────────

class MicWatcher:
    """Polls CoreAudio and fires start/stop callbacks around meetings."""

    def __init__(
        self,
        on_start: Callable[[str, str | None], None],
        on_stop:  Callable[[], None],
    ):
        self.on_start = on_start
        self.on_stop  = on_stop

        self._thread: threading.Thread | None = None
        self._running = False
        self._state   = "idle"   # idle | warming | recording | cooling
        self._since:       datetime | None = None
        self._rec_started: datetime | None = None
        self._browser_source = False  # set when the current recording is a browser meeting
        self._browser_tick   = 0      # counter for periodic browser tab re-check

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._state   = "idle"
        self._since   = None
        self._thread  = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        log.info("watcher started (warmup=%ds grace=%ds)", WARMUP_SECS, GRACE_SECS)

    def stop(self) -> None:
        self._running = False
        log.info("watcher stopped")

    @property
    def is_watching(self) -> bool:
        return self._running

    @property
    def state(self) -> str:
        return self._state

    # ── event loop ────────────────────────────────────────────────────────────

    def _loop(self) -> None:
        while self._running:
            # While recording/cooling, exclude trnscrb's own mic capture so
            # the idle signal can fire when the meeting app releases the mic.
            if self._state in ("recording", "cooling"):
                active = _is_mic_active_externally()
            else:
                active = is_mic_in_use()

            now     = datetime.now()
            elapsed = (now - self._since).total_seconds() if self._since else 0

            if self._state == "idle":
                if active:
                    self._set_state("warming", now)

            elif self._state == "warming":
                if not active:
                    # False positive — Siri, dictation, a brief mic check
                    self._set_state("idle", None)
                elif elapsed >= WARMUP_SECS:
                    meeting = _identify_meeting()
                    if meeting is not None:
                        source, name, bundle_id = meeting
                        log.info("meeting detected: %s (%s)", name, source)
                        self._rec_started    = now
                        # Only use tab-gone detection when tab was verified
                        self._browser_source = (source == "browser")
                        self._browser_tick   = 0
                        self._set_state("recording", now)
                        self.on_start(name, bundle_id)
                    # else: stay in warming — no recognised meeting yet.

            elif self._state == "recording":
                if not active and not is_mic_in_use():
                    # Both per-process AND device-level confirm mic idle.
                    self._set_state("cooling", now)
                elif not active:
                    # Per-process says idle but device says active.
                    # Likely an invisible content process (Firefox).
                    # Use periodic tab check as the stop signal.
                    self._browser_tick += 1
                    if self._browser_tick >= BROWSER_CHECK_EVERY:
                        self._browser_tick = 0
                        if _browser_meeting_name() is None:
                            log.info("meeting tab gone (content-process fallback)")
                            self._set_state("cooling", now)
                elif self._browser_source:
                    # Browsers can keep the mic "warm" after a call ends, so
                    # re-check the tab every few polls.
                    self._browser_tick += 1
                    if self._browser_tick >= BROWSER_CHECK_EVERY:
                        self._browser_tick = 0
                        if _browser_meeting_name() is None:
                            log.info("browser meeting tab gone")
                            self._set_state("cooling", now)

            elif self._state == "cooling":
                if active and not (
                    # Don't rejoin a browser recording if the meeting tab is
                    # already gone — otherwise Chrome keeping the mic warm
                    # would flip-flop us between cooling and recording until
                    # the heat death of the universe.
                    self._browser_source and _browser_meeting_name() is None
                ):
                    self._browser_tick = 0
                    self._set_state("recording", now)
                elif elapsed >= GRACE_SECS:
                    self._rec_started    = None
                    self._browser_source = False
                    self._set_state("idle", None)
                    self.on_stop()

            time.sleep(POLL_SECS)

    def _set_state(self, new_state: str, since: datetime | None) -> None:
        if new_state != self._state:
            log.info("state: %s → %s", self._state, new_state)
        self._state = new_state
        self._since = since


# ── CoreAudio mic detection ───────────────────────────────────────────────────

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


def _is_mic_active_externally() -> bool:
    """True if any process OTHER than trnscrb is using the microphone.

    Uses the per-process CoreAudio API (macOS 14+).  On older macOS the API
    is unavailable and we fall back to the device-level check, which will
    report trnscrb's own recording as active — in that case stop detection
    relies on the meeting app releasing the mic first.
    """
    pids = _pids_using_mic_input()
    if pids is None:
        return is_mic_in_use()
    return bool(pids)


def _pids_using_mic_input() -> set[int] | None:
    """PIDs currently capturing audio input, excluding trnscrb and children.

    Uses kAudioHardwarePropertyProcessObjectList (macOS 14+).  Returns None
    if the API is unavailable so callers can fall back to the device-level
    check.
    """
    try:
        ca = ctypes.CDLL(
            "/System/Library/Frameworks/CoreAudio.framework/CoreAudio"
        )
        addr = _PropAddr(_kProcessObjectList, _kScopeGlobal, _kElementMain)
        sz = ctypes.c_uint32(0)
        if ca.AudioObjectGetPropertyDataSize(
            _kSysObject, ctypes.byref(addr), 0, None, ctypes.byref(sz)
        ) != 0:
            return None
        if sz.value == 0:
            return set()

        n = sz.value // ctypes.sizeof(ctypes.c_uint32)
        objs = (ctypes.c_uint32 * n)()
        if ca.AudioObjectGetPropertyData(
            _kSysObject, ctypes.byref(addr), 0, None,
            ctypes.byref(sz), ctypes.byref(objs)
        ) != 0:
            return None

        exclude = {os.getpid()} | _child_pids
        pids: set[int] = set()
        for obj_id in objs:
            addr_in = _PropAddr(_kProcessIsRunningIn, _kScopeGlobal, _kElementMain)
            running = ctypes.c_uint32(0)
            sz_r = ctypes.c_uint32(ctypes.sizeof(running))
            if ca.AudioObjectGetPropertyData(
                obj_id, ctypes.byref(addr_in), 0, None,
                ctypes.byref(sz_r), ctypes.byref(running)
            ) != 0 or not running.value:
                continue
            addr_pid = _PropAddr(_kProcessPID, _kScopeGlobal, _kElementMain)
            pid = ctypes.c_int32(0)
            sz_p = ctypes.c_uint32(ctypes.sizeof(pid))
            if ca.AudioObjectGetPropertyData(
                obj_id, ctypes.byref(addr_pid), 0, None,
                ctypes.byref(sz_p), ctypes.byref(pid)
            ) == 0 and pid.value not in exclude:
                pids.add(pid.value)
        return pids
    except Exception:
        return None


# ── Meeting identification ───────────────────────────────────────────────────

def _exe_path_of_pid(pid: int) -> str:
    """Return the full executable path of ``pid``, or "" on failure."""
    try:
        r = subprocess.run(
            ["ps", "-p", str(pid), "-o", "comm="],
            capture_output=True, text=True, timeout=2,
        )
        return r.stdout.strip()
    except Exception:
        return ""


def _mic_holder_paths() -> list[str]:
    """Executable paths of every external process currently holding the mic."""
    pids = _pids_using_mic_input()
    if not pids:
        return []
    out: list[str] = []
    for pid in pids:
        path = _exe_path_of_pid(pid)
        if path:
            out.append(path)
    return out


def _identify_meeting() -> tuple[str, str, str | None] | None:
    """Decide whether a recognised meeting is currently in progress.

    Returns ``(source, meeting_name, bundle_id)`` where ``source`` is
    ``"native"``, ``"browser"`` (tab verified), or ``"browser-unverified"``
    (browser holds mic but tab check failed), or ``None`` if no meeting is
    detected.

    Detection strategy (in order):
    1. Per-process CoreAudio API — identifies which PID holds the mic.
    2. Fallback — if mic is active but no PID claims input (e.g. Firefox
       content processes), check ``ps`` for any running meeting app/browser.
    """
    holder_paths = _mic_holder_paths()

    if holder_paths:
        log.debug("mic holders: %s", holder_paths)
        result = _match_holder_paths(holder_paths)
        if result:
            return result

    # Fallback: mic is in use (caller verified) but the per-process API
    # can't see the holder (Firefox content processes, sandboxed helpers,
    # etc.).  Check if any known meeting app or browser is running.
    if not holder_paths:
        result = _match_running_processes()
        if result:
            return result

    return None


def _match_holder_paths(
    holder_paths: list[str],
) -> tuple[str, str, str | None] | None:
    """Match mic-holder executable paths against known apps/browsers."""
    # 1. Native meeting apps — path match is enough.
    for path in holder_paths:
        p = path.lower()
        for frag, name, bundle_id in _NATIVE_APPS:
            if frag.lower() in p:
                return "native", name, bundle_id

    # 2. Browser meetings — mic hold is enough to start; tab check is
    #    best-effort for naming and stop-detection.
    browser_bundle_id: str | None = None
    for path in holder_paths:
        p = path.lower()
        for frag, bundle_id in zip(_BROWSER_BUNDLES, _BROWSER_BUNDLE_IDS):
            if frag.lower() in p:
                browser_bundle_id = bundle_id
                break
        if browser_bundle_id:
            break

    if browser_bundle_id:
        return _resolve_browser_meeting(browser_bundle_id)
    return None


def _match_running_processes() -> tuple[str, str, str | None] | None:
    """Fallback: check browser tab titles when per-process CoreAudio can't
    see the mic holder (e.g. Firefox content processes).

    Only triggers when a browser actually has a recognised meeting tab —
    a running browser alone is NOT sufficient (too many false positives).
    """
    result = _browser_meeting_name()
    if result:
        name, bundle_id = result
        log.info("fallback: browser meeting tab detected: %s", name)
        return "browser", name, bundle_id
    return None


def _resolve_browser_meeting(
    bundle_id: str,
) -> tuple[str, str, str | None]:
    """Given a browser bundle ID, try the tab check for naming, else fallback."""
    result = _browser_meeting_name()
    if result:
        name, _ = result
        log.info("browser meeting tab detected: %s", name)
        return "browser", name, bundle_id
    log.info("browser holding mic but tab check failed; starting anyway")
    return "browser-unverified", _calendar_meeting_name(), bundle_id


def _calendar_meeting_name() -> str:
    """Best-effort meeting name from the macOS calendar, or a timestamp."""
    try:
        from trnscrb.calendar_integration import get_current_or_upcoming_event
        evt = get_current_or_upcoming_event()
        if evt and evt.get("title"):
            return evt["title"]
    except Exception:
        pass
    return f"meeting-{datetime.now().strftime('%H%M')}"


def detect_meeting() -> tuple[str, str | None]:
    """Public: best-effort ``(meeting_name, bundle_id)`` for the *current*
    meeting.

    Used by the ``trnscrb mic-status`` CLI command for diagnostics.  The
    watcher itself calls :func:`_identify_meeting` which returns an
    additional ``source`` field.
    """
    meeting = _identify_meeting()
    if meeting is not None:
        _, name, bundle_id = meeting
        return name, bundle_id
    return _calendar_meeting_name(), None


# AppleScript fragments to read the active tab/window title of each browser.
# Used only by detect_meeting() to find a human-readable meeting name when
# a browser is the source — never used for start/stop gating.
_CHROME_TAB_SCRIPT = """
tell application "System Events"
    if not (exists process "Google Chrome") then return ""
end tell
tell application "Google Chrome"
    repeat with w in windows
        repeat with t in tabs of w
            set u to URL of t
            if u contains "meet.google.com" then
                if u ends with "/landing" or u is "https://meet.google.com/" then return ""
                if (title of t contains "ended") or (title of t contains "left") then return ""
                return "Google Meet"
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
            if u contains "meet.google.com" then
                if u does not end with "/landing" and u is not "https://meet.google.com/" then
                    set t to name of current tab of w
                    if t contains "ended" or t contains "left" then return ""
                    return "Google Meet"
                end if
            end if
            if u contains "teams.microsoft.com" then return "Microsoft Teams"
        end try
    end repeat
end tell
return ""
"""

_FIREFOX_WINDOW_SCRIPT = """
tell application "System Events"
    if not (exists process "firefox") then return ""
end tell
tell application "Firefox"
    repeat with w in windows
        set t to name of w
        if t is "Meet" or t starts with "Meet -" or t starts with "Meet —" then
            if t does not contain "ended" then return "Google Meet"
        end if
        if t contains "Microsoft Teams" then return "Microsoft Teams"
        if t contains "Zoom Meeting" then return "Zoom"
    end repeat
end tell
return ""
"""

_BROWSER_SCRIPTS: list[tuple[str, str]] = [
    (_CHROME_TAB_SCRIPT,     "com.google.Chrome"),
    (_SAFARI_TAB_SCRIPT,     "com.apple.Safari"),
    (_FIREFOX_WINDOW_SCRIPT, "org.mozilla.firefox"),
]


def _browser_meeting_name() -> tuple[str, str] | None:
    """Return ``(meeting_name, browser_bundle_id)`` if any browser has a
    recognised meeting tab open, else None.  Naming only — never used for
    start/stop gating."""
    for script, browser_bundle in _BROWSER_SCRIPTS:
        try:
            r = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=4,
            )
            name = r.stdout.strip()
            if name:
                return name, browser_bundle
        except Exception:
            pass
    return None
