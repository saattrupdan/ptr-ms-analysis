"""A real desktop window for the review app — optional, and invisible without it.

``ptr app`` normally hands its URL to the user's browser, which is fine from a terminal
and awkward from a double-clicked bundle: a tab is one window among many, and it
outlives the server it points at. pywebview gives the app a window of its own instead,
with no address bar and no back button, and its file dialog is the desktop's own dialog
rather than a subprocess called on the user's behalf.

pywebview is an extra (``pip install 'ptr-ms-analysis[desktop]'``), never a core
dependency, so it is imported inside functions only: importing this module — or the app
— must not need it. Everything a GUI can fail to do arrives as
:func:`DesktopUnavailable`, so a caller's decision is always "window or browser" and
never a traceback out of someone else's package.
"""

from __future__ import annotations

import importlib
import importlib.util
import threading

from . import brand

DEFAULT_TITLE = brand.PAGE_TITLE
DEFAULT_WIDTH = 1280
DEFAULT_HEIGHT = 860

# pywebview wants 'Description (*.ext)' — see webview.util.parse_file_type.
H5_FILE_TYPES = ("IONICON runs (*.h5)", "All files (*.*)")


class DesktopUnavailable(Exception):
    """No desktop window here: the extra is missing, or the GUI cannot start."""


# The window run_window() is currently showing. The server needs a handle on it for
# two routes — Browse asks it for a dialog, Stop closes it — and it is the only piece
# of desktop state the rest of the package is allowed to see.
_lock = threading.Lock()
_active = None


def _import_webview():
    """Import pywebview, or explain why this machine cannot do windows.

    Called from inside every function here, never at module scope: the package has to
    stay importable — and useful — without the extra installed.
    """
    if importlib.util.find_spec("webview") is None:
        raise DesktopUnavailable(
            "the desktop extra is not installed "
            "(pip install 'ptr-ms-analysis[desktop]')"
        )
    try:
        return importlib.import_module("webview")
    except Exception as exc:
        # The extra is installed — it is something pywebview itself needs that is
        # missing or broken. In a frozen bundle that is usually a dependency the
        # packer never saw, because this module reaches webview through a string,
        # so say what failed rather than blaming the user's install.
        raise DesktopUnavailable(
            f"pywebview is installed but would not load ({type(exc).__name__}: {exc})"
        ) from exc


def _describe(exc: BaseException) -> str:
    """One line about a toolkit failure, without pretending to know its class names."""
    text = str(exc).strip()
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def available() -> bool:
    """True when a desktop window is possible at all.

    An import probe and nothing else: no window is opened and no event loop starts, so
    it is safe to ask before deciding how to show the app.
    """
    try:
        _import_webview()
    except DesktopUnavailable:
        return False
    return True


def current_window():
    """The live window, or ``None`` when the app is running in a browser tab.

    ``None`` before the loop has created the window, which is the honest answer: a
    dialog asked for in that instant belongs to the subprocess route instead.
    """
    with _lock:
        return _active


def close_window() -> bool:
    """Close the live window, if there is one, and report whether one went away.

    Idempotent on purpose. The page's Stop button closes the window and the window's
    own close handler stops the server, so a second close would be the start of a loop.
    Raises :func:`DesktopUnavailable` if the toolkit refuses, leaving the caller to
    say so; the server is already stopping by then, so it only ever reports.
    """
    global _active
    with _lock:
        window, _active = _active, None
    if window is None:
        return False
    close = getattr(window, "destroy", None) or getattr(window, "close", None)
    if close is None:  # a window this version cannot close
        raise DesktopUnavailable("this pywebview window cannot be closed")
    try:
        close()
    except Exception as exc:
        raise DesktopUnavailable(
            f"the window would not close ({_describe(exc)})"
        ) from exc
    return True


def run_window(
    url,
    *,
    title: str = DEFAULT_TITLE,
    on_close=None,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
):
    """Open ``url`` in one window with no navigation chrome and block in the loop.

    Returns when the window closes, having called ``on_close`` exactly once — from the
    close event if the toolkit fired it, and from here if only the loop ended. Raises
    :func:`DesktopUnavailable` if the extra is missing or the GUI cannot start, in
    which case ``on_close`` is *not* called and the session stays alive.

    The native loop takes over the calling thread, so this belongs on the main thread
    with the server running behind it on a daemon thread.
    """
    global _active
    webview = _import_webview()
    try:
        # No menu and no toolbar: pywebview windows carry neither, which is the point
        # of a window that is an app rather than a browser tab.
        window = webview.create_window(title, url=url, width=width, height=height)
    except Exception as exc:
        raise DesktopUnavailable(f"could not open a window ({_describe(exc)})") from exc
    if window is None:
        raise DesktopUnavailable("pywebview opened no window")

    fired = threading.Event()

    def closed(*_args, **_kwargs):
        if fired.is_set():
            return  # one close per window; the loop ending is not a second one
        fired.set()
        if on_close is not None:
            on_close()

    try:  # best effort — if the event is missing, the loop still reports the end
        window.events.closed += closed
    except Exception:
        pass

    with _lock:
        _active = window
    try:
        webview.start()
    except Exception as exc:
        with _lock:
            if _active is window:
                _active = None
        raise DesktopUnavailable(
            f"the desktop loop would not run ({_describe(exc)})"
        ) from exc
    with _lock:
        if _active is window:
            _active = None
    closed()


def pick_file(window) -> str | None:
    """Ask ``window``'s own toolkit for a ``.h5`` file, or ``None`` if cancelled.

    Goes through the window rather than a subprocess because the dialog then belongs to
    the app the reviewer is already looking at, instead of opening behind it.
    """
    webview = _import_webview()
    try:
        picked = window.create_file_dialog(
            _open_flag(webview), file_types=H5_FILE_TYPES
        )
    except Exception as exc:
        raise DesktopUnavailable(
            f"the file dialog would not open ({_describe(exc)})"
        ) from exc
    if not picked:
        return None
    first = picked[0] if isinstance(picked, (list, tuple)) else picked
    return str(first) or None


def _open_flag(webview) -> int:
    """pywebview's "open a file" constant, under whichever name this version uses.

    6.x renamed ``OPEN_FILE_DIALOG`` to ``FileDialog.OPEN`` and left a deprecated module
    attribute behind that prints when touched, so the enum comes first and the names are
    only fallen back on for older releases.
    """
    flag = getattr(getattr(webview, "FileDialog", None), "OPEN", None)
    if flag is not None:
        return int(flag)
    for name in ("OPEN_FILE_DIALOG", "OPEN_DIALOG"):
        flag = getattr(webview, name, None)
        if flag is not None:
            return int(flag)
    return 10  # create_file_dialog's own default in every version checked
