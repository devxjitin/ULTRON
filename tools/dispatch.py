"""Routes tool calls to the concrete Python implementation."""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

import pyautogui

from control.locator import (
    click_on_description,
    drag_between_descriptions,
    locate_target_on_screen,
    move_to_description,
)
from control.mouse_keyboard import (
    click_mouse,
    drag_mouse,
    get_clipboard_text,
    get_computer_control_status,
    key_down,
    key_up,
    mouse_down,
    mouse_up,
    move_mouse,
    press_hotkey,
    press_key,
    scroll_mouse,
    type_text,
    wait_for_screen,
)
from control.windows import focus_window, launch_application, list_open_windows
from memory.db import (
    forget_memory,
    list_memories,
    recall_recent_conversation,
    recent_terminal_history,
    remember_memory,
    search_memory,
)
from camera.capture import get_camera_capture_status, set_camera_capture
from screen.capture import get_screen_capture_status, set_screen_capture
from terminal.shell import run_cmd_command, run_powershell_command
from config import DEFAULT_COMMAND_TIMEOUT_SECONDS


# ---------------------------------------------------------------------------
# GUI handlers (mouse/keyboard/pointing). These block on real hardware I/O,
# so they always run in a worker thread via asyncio.to_thread.
#
# These are plain module-level functions rather than lambdas built fresh
# inside dispatch_tool() on every call: the previous version reconstructed a
# dict of a dozen-plus closures on every single tool invocation (including
# for terminal/memory tools that never touch this dict at all), which is
# needless per-call allocation on a hot path that fires for every user turn.
# ---------------------------------------------------------------------------


def _get_computer_control_status(arguments: dict[str, Any]) -> dict[str, Any]:
    return get_computer_control_status()


def _locate_target_on_screen(arguments: dict[str, Any]) -> dict[str, Any]:
    return locate_target_on_screen(arguments.get("description", ""))


def _click_on_description(arguments: dict[str, Any]) -> dict[str, Any]:
    return click_on_description(
        arguments.get("description", ""),
        arguments.get("button", "left"),
        arguments.get("clicks", 1),
        arguments.get("interval_seconds", 0.12),
    )


def _move_to_description(arguments: dict[str, Any]) -> dict[str, Any]:
    return move_to_description(
        arguments.get("description", ""),
        arguments.get("duration_seconds", 0.2),
    )


def _drag_between_descriptions(arguments: dict[str, Any]) -> dict[str, Any]:
    return drag_between_descriptions(
        arguments.get("start_description", ""),
        arguments.get("end_description", ""),
        arguments.get("duration_seconds", 0.7),
        arguments.get("button", "left"),
    )


def _move_mouse(arguments: dict[str, Any]) -> dict[str, Any]:
    return move_mouse(
        arguments.get("x"),
        arguments.get("y"),
        arguments.get("duration_seconds", 0.2),
    )


def _click_mouse(arguments: dict[str, Any]) -> dict[str, Any]:
    return click_mouse(
        arguments.get("x"),
        arguments.get("y"),
        arguments.get("button", "left"),
        arguments.get("clicks", 1),
        arguments.get("interval_seconds", 0.12),
    )


def _drag_mouse(arguments: dict[str, Any]) -> dict[str, Any]:
    return drag_mouse(
        arguments.get("start_x"),
        arguments.get("start_y"),
        arguments.get("end_x"),
        arguments.get("end_y"),
        arguments.get("duration_seconds", 0.7),
        arguments.get("button", "left"),
    )


def _scroll_mouse(arguments: dict[str, Any]) -> dict[str, Any]:
    return scroll_mouse(
        arguments.get("vertical_clicks", 0),
        arguments.get("horizontal_clicks", 0),
        arguments.get("x"),
        arguments.get("y"),
    )


def _type_text(arguments: dict[str, Any]) -> dict[str, Any]:
    return type_text(
        arguments.get("text", ""),
        arguments.get("interval_seconds", 0.01),
        arguments.get("press_enter", False),
        arguments.get("use_clipboard", True),
    )


def _press_key(arguments: dict[str, Any]) -> dict[str, Any]:
    return press_key(
        arguments.get("key", ""),
        arguments.get("presses", 1),
        arguments.get("interval_seconds", 0.08),
    )


def _press_hotkey(arguments: dict[str, Any]) -> dict[str, Any]:
    return press_hotkey(arguments.get("keys", []))


def _mouse_down(arguments: dict[str, Any]) -> dict[str, Any]:
    return mouse_down(
        arguments.get("x"),
        arguments.get("y"),
        arguments.get("button", "left"),
    )


def _mouse_up(arguments: dict[str, Any]) -> dict[str, Any]:
    return mouse_up(arguments.get("button", "left"))


def _key_down(arguments: dict[str, Any]) -> dict[str, Any]:
    return key_down(arguments.get("key", ""))


def _key_up(arguments: dict[str, Any]) -> dict[str, Any]:
    return key_up(arguments.get("key", ""))


def _get_clipboard_text(arguments: dict[str, Any]) -> dict[str, Any]:
    return get_clipboard_text()


def _list_open_windows(arguments: dict[str, Any]) -> dict[str, Any]:
    return list_open_windows()


def _focus_window(arguments: dict[str, Any]) -> dict[str, Any]:
    return focus_window(arguments.get("title", ""))


def _launch_application(arguments: dict[str, Any]) -> dict[str, Any]:
    return launch_application(arguments.get("target", ""))


def _wait_for_screen(arguments: dict[str, Any]) -> dict[str, Any]:
    return wait_for_screen(arguments.get("seconds", 1.0))


def _set_camera_capture(arguments: dict[str, Any]) -> dict[str, Any]:
    # Opening/probing a webcam device can take up to a second or two, so —
    # unlike the cheap screen-state handlers below — this runs in a worker
    # thread rather than blocking the asyncio event loop.
    camera_index = arguments.get("camera_index")
    return set_camera_capture(
        enabled=arguments.get("enabled", True),
        camera_index=int(camera_index) if camera_index is not None else None,
    )


def _get_camera_capture_status(arguments: dict[str, Any]) -> dict[str, Any]:
    return get_camera_capture_status()


GUI_HANDLERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "get_computer_control_status": _get_computer_control_status,
    "locate_target_on_screen": _locate_target_on_screen,
    "click_on_description": _click_on_description,
    "move_to_description": _move_to_description,
    "drag_between_descriptions": _drag_between_descriptions,
    "move_mouse": _move_mouse,
    "click_mouse": _click_mouse,
    "drag_mouse": _drag_mouse,
    "mouse_down": _mouse_down,
    "mouse_up": _mouse_up,
    "scroll_mouse": _scroll_mouse,
    "type_text": _type_text,
    "get_clipboard_text": _get_clipboard_text,
    "press_key": _press_key,
    "press_hotkey": _press_hotkey,
    "key_down": _key_down,
    "key_up": _key_up,
    "wait_for_screen": _wait_for_screen,
    "set_camera_capture": _set_camera_capture,
    "get_camera_capture_status": _get_camera_capture_status,
    "list_open_windows": _list_open_windows,
    "focus_window": _focus_window,
    "launch_application": _launch_application,
}


# ---------------------------------------------------------------------------
# Async handlers (shell commands run as real subprocesses).
# ---------------------------------------------------------------------------


async def _run_cmd_command(arguments: dict[str, Any]) -> dict[str, Any]:
    return await run_cmd_command(
        command=arguments.get("command", ""),
        working_directory=arguments.get("working_directory", ""),
        timeout_seconds=arguments.get(
            "timeout_seconds", DEFAULT_COMMAND_TIMEOUT_SECONDS
        ),
    )


async def _run_powershell_command(arguments: dict[str, Any]) -> dict[str, Any]:
    return await run_powershell_command(
        command=arguments.get("command", ""),
        working_directory=arguments.get("working_directory", ""),
        timeout_seconds=arguments.get(
            "timeout_seconds", DEFAULT_COMMAND_TIMEOUT_SECONDS
        ),
    )


ASYNC_HANDLERS: dict[str, Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]] = {
    "run_cmd_command": _run_cmd_command,
    "run_powershell_command": _run_powershell_command,
}


# ---------------------------------------------------------------------------
# Fast synchronous handlers (screen state and SQLite memory). None of these
# block on hardware I/O long enough to warrant a worker thread.
# ---------------------------------------------------------------------------


def _set_screen_capture(arguments: dict[str, Any]) -> dict[str, Any]:
    monitor_index = arguments.get("monitor_index")
    return set_screen_capture(
        enabled=arguments.get("enabled", True),
        monitor_index=int(monitor_index) if monitor_index is not None else None,
    )


def _get_screen_capture_status(arguments: dict[str, Any]) -> dict[str, Any]:
    return get_screen_capture_status()


def _remember_memory(arguments: dict[str, Any]) -> dict[str, Any]:
    return remember_memory(
        memory_key=arguments.get("memory_key", ""),
        memory_value=arguments.get("memory_value", ""),
        category=arguments.get("category", "general"),
    )


def _search_memory(arguments: dict[str, Any]) -> dict[str, Any]:
    return search_memory(
        query=arguments.get("query", ""),
        limit=arguments.get("limit", 10),
    )


def _list_memories(arguments: dict[str, Any]) -> dict[str, Any]:
    return list_memories(arguments.get("limit", 20))


def _forget_memory(arguments: dict[str, Any]) -> dict[str, Any]:
    memory_id = arguments.get("memory_id")
    return forget_memory(
        memory_id=int(memory_id) if memory_id is not None else None,
        query=arguments.get("query", ""),
    )


def _recall_recent_conversation(arguments: dict[str, Any]) -> dict[str, Any]:
    return recall_recent_conversation(arguments.get("limit", 10))


def _recent_terminal_history(arguments: dict[str, Any]) -> dict[str, Any]:
    return recent_terminal_history(arguments.get("limit", 10))


SYNC_HANDLERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "set_screen_capture": _set_screen_capture,
    "get_screen_capture_status": _get_screen_capture_status,
    "remember_memory": _remember_memory,
    "search_memory": _search_memory,
    "list_memories": _list_memories,
    "forget_memory": _forget_memory,
    "recall_recent_conversation": _recall_recent_conversation,
    "recent_terminal_history": _recent_terminal_history,
}


async def dispatch_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    try:
        async_handler = ASYNC_HANDLERS.get(name)
        if async_handler is not None:
            return await async_handler(arguments)

        gui_handler = GUI_HANDLERS.get(name)
        if gui_handler is not None:
            return await asyncio.to_thread(gui_handler, arguments)

        sync_handler = SYNC_HANDLERS.get(name)
        if sync_handler is not None:
            return sync_handler(arguments)

        return {"status": "error", "message": f"Unknown tool: {name}"}

    except pyautogui.FailSafeException:
        return {
            "status": "aborted",
            "error_type": "PyAutoGUIFailSafeException",
            "message": (
                "GUI automation was stopped because the pointer reached a "
                "screen corner."
            ),
        }
    except Exception as error:
        return {
            "status": "error",
            "error_type": type(error).__name__,
            "message": str(error),
        }
