"""Windows screen capture, monitor selection, and coordinate grid overlay."""

from screen.capture import (
    capture_screen_frame,
    coerce_bool,
    draw_coordinate_grid,
    get_available_monitors,
    get_screen_capture_status,
    set_screen_capture,
)

__all__ = [
    "capture_screen_frame",
    "coerce_bool",
    "draw_coordinate_grid",
    "get_available_monitors",
    "get_screen_capture_status",
    "set_screen_capture",
]
