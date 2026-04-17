"""Screen Recording permission check via CoreGraphics ctypes bindings."""
import ctypes
import ctypes.util

_cg = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreGraphics"))
_cg.CGPreflightScreenCaptureAccess.restype = ctypes.c_bool
_cg.CGRequestScreenCaptureAccess.restype = ctypes.c_bool


def check_permission() -> bool:
    """Return True if Screen Recording permission is currently granted."""
    return _cg.CGPreflightScreenCaptureAccess()


def request_permission() -> bool:
    """Trigger the macOS permission prompt. Returns True if already granted."""
    return _cg.CGRequestScreenCaptureAccess()
