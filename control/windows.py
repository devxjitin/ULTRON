"""Window enumeration/focus and application launching, for human-like app switching.

Clicking a taskbar icon by guessed coordinates is unreliable; these tools
let Ultron switch between already-open windows or start a new application
by name/path directly, the way a human would use Alt-Tab or the Start menu.
"""

from __future__ import annotations

import os
import time
from typing import Any

import pygetwindow as gw

from config import MAX_APPLICATION_LAUNCH_TARGET_CHARACTERS, POST_GUI_ACTION_SETTLE_SECONDS


def list_open_windows() -> dict[str, Any]:
    windows = []
    for window in gw.getAllWindows():
        title = (window.title or "").strip()
        if not title or not window.visible:
            continue
        windows.append(
            {
                "title": title,
                "is_active": bool(window.isActive),
                "is_minimized": bool(window.isMinimized),
                "is_maximized": bool(window.isMaximized),
            }
        )

    return {
        "status": "completed",
        "count": len(windows),
        "windows": windows,
    }


def focus_window(title: str) -> dict[str, Any]:
    """Bring the best-matching already-open window to the foreground.

    Matches case-insensitively against a substring of the window title,
    since callers will rarely know a window's exact full title.
    """
    query = str(title or "").strip()
    if not query:
        raise ValueError("title cannot be empty.")

    candidates = [
        window
        for window in gw.getAllWindows()
        if window.title and window.visible
    ]

    lowered_query = query.lower()
    exact = [w for w in candidates if w.title.strip().lower() == lowered_query]
    partial = [w for w in candidates if lowered_query in w.title.lower()]
    match = (exact or partial or [None])[0]

    if match is None:
        available = sorted({w.title.strip() for w in candidates})
        raise ValueError(
            f"No open window matches {query!r}. "
            f"Currently open window titles: {available}"
        )

    if match.isMinimized:
        match.restore()
    match.activate()
    time.sleep(POST_GUI_ACTION_SETTLE_SECONDS)

    return {
        "status": "completed",
        "action": "focus_window",
        "matched_title": match.title,
        "query": query,
    }


def launch_application(target: str) -> dict[str, Any]:
    """Launch an application, file, folder, or URL, like double-clicking it.

    Accepts anything os.startfile accepts: an executable name already on
    PATH (e.g. "notepad", "calc"), a full path to a program or document, a
    folder path, or a URL — this is the same mechanism as double-clicking
    it in Explorer or typing it into the Windows Run dialog.
    """
    text = str(target or "").strip()
    if not text:
        raise ValueError("target cannot be empty.")
    if len(text) > MAX_APPLICATION_LAUNCH_TARGET_CHARACTERS:
        raise ValueError(
            "target exceeds the "
            f"{MAX_APPLICATION_LAUNCH_TARGET_CHARACTERS}-character limit."
        )
    if os.name != "nt":
        raise RuntimeError("launch_application is only supported on Windows.")

    try:
        os.startfile(text)  # noqa: S606 - intentional, user-directed launch
    except OSError as error:
        raise RuntimeError(f"Could not launch {text!r}: {error}") from error

    time.sleep(POST_GUI_ACTION_SETTLE_SECONDS)
    return {
        "status": "completed",
        "action": "launch_application",
        "target": text,
    }
