"""Windows mouse and keyboard control using normalized 0-1000 coordinates."""

from __future__ import annotations

import ctypes
import os
import time
from typing import Any

import pyautogui
import pyperclip

from config import (
    MAX_GUI_DURATION_SECONDS,
    MAX_KEY_PRESSES,
    MAX_MOUSE_CLICKS,
    MAX_SCROLL_CLICKS,
    MAX_TYPED_TEXT_CHARACTERS,
    MAX_WAIT_SECONDS,
    POST_GUI_ACTION_SETTLE_SECONDS,
    PYAUTOGUI_FAILSAFE,
    PYAUTOGUI_PAUSE_SECONDS,
    SCREEN_STATE,
    SCROLL_WHEEL_DELTA,
)
from screen.capture import coerce_bool, get_available_monitors


def enable_windows_dpi_awareness() -> None:
    """Keep MSS capture and input coordinates aligned on scaled displays."""
    if os.name != "nt":
        return

    try:
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except Exception:
        pass

    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def configure_pyautogui() -> None:
    pyautogui.FAILSAFE = PYAUTOGUI_FAILSAFE
    pyautogui.PAUSE = PYAUTOGUI_PAUSE_SECONDS


def _selected_monitor() -> dict[str, int]:
    monitor_index = int(SCREEN_STATE["monitor_index"])
    monitors = get_available_monitors()
    for monitor in monitors:
        if monitor["index"] == monitor_index:
            return monitor
    raise ValueError(
        f"Selected monitor index {monitor_index} is unavailable. "
        f"Available indexes: {[item['index'] for item in monitors]}"
    )


def _normalized_coordinate(value: Any, name: str) -> float:
    coordinate = float(value)
    if not 0.0 <= coordinate <= 1000.0:
        raise ValueError(f"{name} must be between 0 and 1000.")
    return coordinate


def normalized_to_desktop(x: Any, y: Any) -> dict[str, Any]:
    """Convert 0..1000 frame coordinates into virtual desktop coordinates."""
    normalized_x = _normalized_coordinate(x, "x")
    normalized_y = _normalized_coordinate(y, "y")
    monitor = _selected_monitor()

    pixel_x = int(
        round(monitor["left"] + (normalized_x / 1000.0) * (monitor["width"] - 1))
    )
    pixel_y = int(
        round(monitor["top"] + (normalized_y / 1000.0) * (monitor["height"] - 1))
    )

    return {
        "normalized_x": normalized_x,
        "normalized_y": normalized_y,
        "pixel_x": pixel_x,
        "pixel_y": pixel_y,
        "monitor": monitor,
    }


def _safe_duration(value: Any, default: float = 0.2) -> float:
    duration = float(default if value is None else value)
    return max(0.0, min(duration, MAX_GUI_DURATION_SECONDS))


def _safe_interval(value: Any, default: float = 0.05) -> float:
    interval = float(default if value is None else value)
    return max(0.0, min(interval, 5.0))


def _normalize_mouse_button(button: Any) -> str:
    value = str(button or "left").strip().lower()
    aliases = {"primary": "left", "secondary": "right"}
    value = aliases.get(value, value)
    if value not in {"left", "right", "middle"}:
        raise ValueError("button must be left, right, or middle.")
    return value


def _normalize_key(key: Any) -> str:
    value = str(key).strip().lower()
    aliases = {
        "control": "ctrl",
        "escape": "esc",
        "return": "enter",
        "windows": "win",
        "command": "win",
        "option": "alt",
        "pageup": "pgup",
        "pagedown": "pgdn",
        "delete": "del",
        "spacebar": "space",
    }
    value = aliases.get(value, value)
    if value not in pyautogui.KEYBOARD_KEYS:
        raise ValueError(f"Unsupported key: {value}")
    return value


def get_computer_control_status() -> dict[str, Any]:
    cursor = pyautogui.position()
    return {
        "status": "completed",
        "coordinate_system": (
            "Tools use normalized coordinates: left/top=0, right/bottom=1000."
        ),
        "selected_monitor": _selected_monitor(),
        "cursor_position": {"x": int(cursor.x), "y": int(cursor.y)},
        "pyautogui_failsafe": bool(pyautogui.FAILSAFE),
        "emergency_stop": (
            "Move the pointer rapidly to a screen corner or press Ctrl+C "
            "in the console."
        ),
    }


def move_mouse(x: Any, y: Any, duration_seconds: Any = 0.2) -> dict[str, Any]:
    target = normalized_to_desktop(x, y)
    duration = _safe_duration(duration_seconds)
    pyautogui.moveTo(target["pixel_x"], target["pixel_y"], duration=duration)
    time.sleep(POST_GUI_ACTION_SETTLE_SECONDS)
    return {
        "status": "completed",
        "action": "move_mouse",
        "target": target,
        "duration_seconds": duration,
    }


def click_mouse(
    x: Any,
    y: Any,
    button: Any = "left",
    clicks: Any = 1,
    interval_seconds: Any = 0.12,
) -> dict[str, Any]:
    target = normalized_to_desktop(x, y)
    normalized_button = _normalize_mouse_button(button)
    click_count = max(1, min(int(clicks or 1), MAX_MOUSE_CLICKS))
    interval = _safe_interval(interval_seconds, 0.12)

    pyautogui.click(
        x=target["pixel_x"],
        y=target["pixel_y"],
        clicks=click_count,
        interval=interval,
        button=normalized_button,
    )
    time.sleep(POST_GUI_ACTION_SETTLE_SECONDS)
    return {
        "status": "completed",
        "action": "click_mouse",
        "target": target,
        "button": normalized_button,
        "clicks": click_count,
        "interval_seconds": interval,
    }


def drag_mouse(
    start_x: Any,
    start_y: Any,
    end_x: Any,
    end_y: Any,
    duration_seconds: Any = 0.7,
    button: Any = "left",
) -> dict[str, Any]:
    start = normalized_to_desktop(start_x, start_y)
    end = normalized_to_desktop(end_x, end_y)
    normalized_button = _normalize_mouse_button(button)
    duration = _safe_duration(duration_seconds, 0.7)

    pyautogui.moveTo(start["pixel_x"], start["pixel_y"], duration=0.15)
    pyautogui.dragTo(
        end["pixel_x"],
        end["pixel_y"],
        duration=duration,
        button=normalized_button,
    )
    time.sleep(POST_GUI_ACTION_SETTLE_SECONDS)
    return {
        "status": "completed",
        "action": "drag_mouse",
        "start": start,
        "end": end,
        "button": normalized_button,
        "duration_seconds": duration,
    }


def scroll_mouse(
    vertical_clicks: Any = 0,
    horizontal_clicks: Any = 0,
    x: Any | None = None,
    y: Any | None = None,
) -> dict[str, Any]:
    vertical = max(
        -MAX_SCROLL_CLICKS,
        min(int(vertical_clicks or 0), MAX_SCROLL_CLICKS),
    )
    horizontal = max(
        -MAX_SCROLL_CLICKS,
        min(int(horizontal_clicks or 0), MAX_SCROLL_CLICKS),
    )

    target = None
    if x is not None or y is not None:
        if x is None or y is None:
            raise ValueError("Provide both x and y, or neither.")
        target = normalized_to_desktop(x, y)
        pyautogui.moveTo(target["pixel_x"], target["pixel_y"], duration=0.1)

    # pyautogui.scroll()/hscroll() pass their argument straight through as
    # the raw Win32 wheel delta with no scaling, and a real wheel notch is
    # SCROLL_WHEEL_DELTA (120) of that unit — so without this multiplier,
    # "3 clicks" barely moved the page at all. Scaling here means the
    # public API keeps using intuitive small "click" counts.
    if vertical:
        pyautogui.scroll(vertical * SCROLL_WHEEL_DELTA)
    if horizontal:
        pyautogui.hscroll(horizontal * SCROLL_WHEEL_DELTA)

    time.sleep(POST_GUI_ACTION_SETTLE_SECONDS)
    return {
        "status": "completed",
        "action": "scroll_mouse",
        "vertical_clicks": vertical,
        "horizontal_clicks": horizontal,
        "target": target,
        "direction_note": (
            "Positive vertical values scroll up; negative values scroll down."
        ),
    }


def type_text(
    text: Any,
    interval_seconds: Any = 0.01,
    press_enter: Any = False,
    use_clipboard: Any = True,
) -> dict[str, Any]:
    content = str(text)
    if not content:
        raise ValueError("text cannot be empty.")
    if len(content) > MAX_TYPED_TEXT_CHARACTERS:
        raise ValueError(
            f"text exceeds the {MAX_TYPED_TEXT_CHARACTERS}-character limit."
        )

    interval = _safe_interval(interval_seconds, 0.01)
    clipboard_mode = coerce_bool(use_clipboard)
    should_press_enter = coerce_bool(press_enter)

    if clipboard_mode:
        previous_clipboard: str | None
        try:
            previous_clipboard = pyperclip.paste()
        except Exception:
            previous_clipboard = None

        pyperclip.copy(content)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(max(0.2, POST_GUI_ACTION_SETTLE_SECONDS))

        if previous_clipboard is not None:
            try:
                pyperclip.copy(previous_clipboard)
            except Exception:
                pass
    else:
        pyautogui.write(content, interval=interval)

    if should_press_enter:
        pyautogui.press("enter")

    time.sleep(POST_GUI_ACTION_SETTLE_SECONDS)
    return {
        "status": "completed",
        "action": "type_text",
        "characters": len(content),
        "method": "clipboard_paste" if clipboard_mode else "key_by_key",
        "pressed_enter": should_press_enter,
    }


def press_key(
    key: Any,
    presses: Any = 1,
    interval_seconds: Any = 0.08,
) -> dict[str, Any]:
    normalized_key = _normalize_key(key)
    press_count = max(1, min(int(presses or 1), MAX_KEY_PRESSES))
    interval = _safe_interval(interval_seconds, 0.08)
    pyautogui.press(normalized_key, presses=press_count, interval=interval)
    time.sleep(POST_GUI_ACTION_SETTLE_SECONDS)
    return {
        "status": "completed",
        "action": "press_key",
        "key": normalized_key,
        "presses": press_count,
        "interval_seconds": interval,
    }


def press_hotkey(keys: Any) -> dict[str, Any]:
    if not isinstance(keys, (list, tuple)) or not keys:
        raise ValueError("keys must be a non-empty list, such as ['ctrl', 's'].")
    if len(keys) > 8:
        raise ValueError("A hotkey may contain at most 8 keys.")

    normalized_keys = [_normalize_key(key) for key in keys]
    pyautogui.hotkey(*normalized_keys)
    time.sleep(POST_GUI_ACTION_SETTLE_SECONDS)
    return {
        "status": "completed",
        "action": "press_hotkey",
        "keys": normalized_keys,
    }


def mouse_down(x: Any, y: Any, button: Any = "left") -> dict[str, Any]:
    """Press and hold a mouse button at a location without releasing it.

    Pairs with mouse_up for things click_mouse/drag_mouse can't do in one
    step, e.g. holding then dragging across several intermediate points, or
    press-holding a UI element that reacts to hold duration.
    """
    target = normalized_to_desktop(x, y)
    normalized_button = _normalize_mouse_button(button)
    pyautogui.moveTo(target["pixel_x"], target["pixel_y"], duration=0.1)
    pyautogui.mouseDown(button=normalized_button)
    return {
        "status": "completed",
        "action": "mouse_down",
        "target": target,
        "button": normalized_button,
        "warning": "Button is now held down. Call mouse_up to release it.",
    }


def mouse_up(button: Any = "left") -> dict[str, Any]:
    """Release a mouse button previously held with mouse_down."""
    normalized_button = _normalize_mouse_button(button)
    pyautogui.mouseUp(button=normalized_button)
    time.sleep(POST_GUI_ACTION_SETTLE_SECONDS)
    return {
        "status": "completed",
        "action": "mouse_up",
        "button": normalized_button,
    }


def key_down(key: Any) -> dict[str, Any]:
    """Press and hold a key without releasing it.

    Pairs with key_up for things press_key/press_hotkey can't do, e.g.
    holding Shift while making several separate clicks to multi-select, or
    holding an arrow key for continuous movement.
    """
    normalized_key = _normalize_key(key)
    pyautogui.keyDown(normalized_key)
    return {
        "status": "completed",
        "action": "key_down",
        "key": normalized_key,
        "warning": "Key is now held down. Call key_up to release it.",
    }


def key_up(key: Any) -> dict[str, Any]:
    """Release a key previously held with key_down."""
    normalized_key = _normalize_key(key)
    pyautogui.keyUp(normalized_key)
    time.sleep(POST_GUI_ACTION_SETTLE_SECONDS)
    return {
        "status": "completed",
        "action": "key_up",
        "key": normalized_key,
    }


def get_clipboard_text() -> dict[str, Any]:
    """Read the current clipboard contents without changing them."""
    try:
        text = pyperclip.paste()
    except Exception as error:
        raise RuntimeError(f"Could not read clipboard: {error}") from error

    return {
        "status": "completed",
        "action": "get_clipboard_text",
        "text": text,
        "characters": len(text),
    }


def wait_for_screen(seconds: Any = 1.0) -> dict[str, Any]:
    duration = max(0.0, min(float(seconds or 1.0), MAX_WAIT_SECONDS))
    time.sleep(duration)
    return {
        "status": "completed",
        "action": "wait_for_screen",
        "seconds": duration,
    }
