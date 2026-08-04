"""Explicit, user-started continuous task mode (e.g. "keep replying on WhatsApp")."""

from continuous_task.state import (
    get_continuous_task_status,
    start_continuous_task,
    stop_continuous_task,
)

__all__ = [
    "get_continuous_task_status",
    "start_continuous_task",
    "stop_continuous_task",
]
