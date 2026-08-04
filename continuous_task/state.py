"""Start/stop/status for continuous task mode (see voice.assistant's task-nudge loop)."""

from __future__ import annotations

from typing import Any

from config import CONTINUOUS_TASK_STATE

MAX_TASK_DESCRIPTION_CHARACTERS = 500


def start_continuous_task(description: str) -> dict[str, Any]:
    text = str(description or "").strip()
    if not text:
        raise ValueError("description cannot be empty.")
    if len(text) > MAX_TASK_DESCRIPTION_CHARACTERS:
        text = text[:MAX_TASK_DESCRIPTION_CHARACTERS]

    CONTINUOUS_TASK_STATE["active"] = True
    CONTINUOUS_TASK_STATE["description"] = text

    return {
        "status": "completed",
        "continuous_task_active": True,
        "description": text,
    }


def stop_continuous_task() -> dict[str, Any]:
    CONTINUOUS_TASK_STATE["active"] = False
    description = CONTINUOUS_TASK_STATE["description"]
    CONTINUOUS_TASK_STATE["description"] = ""

    return {
        "status": "completed",
        "continuous_task_active": False,
        "stopped_description": description,
    }


def get_continuous_task_status() -> dict[str, Any]:
    return {
        "status": "completed",
        "continuous_task_active": CONTINUOUS_TASK_STATE["active"],
        "description": CONTINUOUS_TASK_STATE["description"],
    }
