"""Windows screen capture: monitor enumeration, frame capture, and grid overlay."""

from __future__ import annotations

from io import BytesIO
from typing import Any

import mss
import pyautogui
from PIL import Image, ImageDraw

from config import (
    CURSOR_MARKER_RADIUS,
    GRID_LABEL_COLOR,
    GRID_LINE_COLOR,
    GRID_STEP,
    SCREEN_FPS,
    SCREEN_JPEG_QUALITY,
    SCREEN_MAX_DIMENSION,
    SCREEN_STATE,
    SHOW_COORDINATE_GRID,
    SHOW_CURSOR_IN_SCREEN_STREAM,
)


def coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)

    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes", "on", "enable", "enabled"}:
        return True
    if normalized in {"false", "0", "no", "off", "disable", "disabled"}:
        return False
    raise ValueError(f"Cannot interpret {value!r} as a boolean.")


def get_available_monitors() -> list[dict[str, int]]:
    """Return mss monitor indexes and their capture rectangles."""
    with mss.mss() as screen_capture:
        monitors: list[dict[str, int]] = []
        for index, monitor in enumerate(screen_capture.monitors):
            monitors.append(
                {
                    "index": index,
                    "left": int(monitor["left"]),
                    "top": int(monitor["top"]),
                    "width": int(monitor["width"]),
                    "height": int(monitor["height"]),
                }
            )
        return monitors


def set_screen_capture(
    enabled: bool,
    monitor_index: int | None = None,
) -> dict[str, Any]:
    """Enable/disable screen vision and optionally select a monitor."""
    is_enabled = coerce_bool(enabled)
    monitors = get_available_monitors()

    if monitor_index is not None:
        selected_index = int(monitor_index)
        valid_indexes = {monitor["index"] for monitor in monitors}
        if selected_index not in valid_indexes:
            raise ValueError(
                f"Invalid monitor_index {selected_index}. "
                f"Available indexes: {sorted(valid_indexes)}. "
                "Index 0 captures all monitors together."
            )
        SCREEN_STATE["monitor_index"] = selected_index

    SCREEN_STATE["enabled"] = is_enabled
    if is_enabled:
        SCREEN_STATE["last_error"] = None

    return {
        "status": "completed",
        "screen_capture_enabled": SCREEN_STATE["enabled"],
        "monitor_index": SCREEN_STATE["monitor_index"],
        "available_monitors": monitors,
        "note": "Monitor index 0 captures the entire virtual desktop.",
    }


def get_screen_capture_status() -> dict[str, Any]:
    try:
        monitors = get_available_monitors()
    except Exception as error:
        monitors = []
        SCREEN_STATE["last_error"] = f"{type(error).__name__}: {error}"

    return {
        "status": "completed",
        "screen_capture_enabled": SCREEN_STATE["enabled"],
        "monitor_index": SCREEN_STATE["monitor_index"],
        "frame_rate": SCREEN_FPS,
        "max_dimension": SCREEN_MAX_DIMENSION,
        "jpeg_quality": SCREEN_JPEG_QUALITY,
        "last_frame_at": SCREEN_STATE["last_frame_at"],
        "last_frame_width": SCREEN_STATE["last_frame_width"],
        "last_frame_height": SCREEN_STATE["last_frame_height"],
        "last_frame_bytes": SCREEN_STATE["last_frame_bytes"],
        "last_error": SCREEN_STATE["last_error"],
        "available_monitors": monitors,
    }


def draw_coordinate_grid(image: Image.Image) -> None:
    """Overlay faint gridlines labeled with normalized 0-1000 coordinates.

    This is the main fix for inconsistent click accuracy: without visible
    reference points, the model has to guess a target's position on the
    normalized 0-1000 scale purely by eye, which produces clicks that are
    "close but not exact" or occasionally far off. Labeled gridlines let it
    read the nearest intersection (e.g. "600,300") and reason about a small
    offset from there instead, which is dramatically more reliable.
    """
    width, height = image.size
    draw = ImageDraw.Draw(image, "RGBA")
    line_color = GRID_LINE_COLOR + (90,)
    label_bg = (0, 0, 0, 160)

    steps = list(range(0, 1001, GRID_STEP))
    for value in steps:
        px = int(round((value / 1000.0) * (width - 1)))
        draw.line((px, 0, px, height), fill=line_color, width=1)

    for value in steps:
        py = int(round((value / 1000.0) * (height - 1)))
        draw.line((0, py, width, py), fill=line_color, width=1)

    # Labels at every intersection would be too cluttered; label along the
    # top edge and left edge only, which is enough to read any intersection.
    for value in steps:
        px = int(round((value / 1000.0) * (width - 1)))
        text = str(value)
        draw.rectangle((px + 1, 1, px + 1 + 8 * len(text), 13), fill=label_bg)
        draw.text((px + 2, 1), text, fill=GRID_LABEL_COLOR)

    for value in steps:
        py = int(round((value / 1000.0) * (height - 1)))
        text = str(value)
        draw.rectangle((1, py + 1, 1 + 8 * len(text), py + 13), fill=label_bg)
        draw.text((2, py + 1), text, fill=GRID_LABEL_COLOR)


def capture_screen_image() -> tuple[Image.Image, dict[str, Any]]:
    """Capture the selected Windows monitor and return a PIL image + metadata,
    with the cursor marker drawn and downscaled to SCREEN_MAX_DIMENSION, but
    *before* the coordinate grid overlay or JPEG encoding -- shared by
    capture_screen_frame (screen-only) and capture_combined_frame (screen +
    camera picture-in-picture), which each finish it differently.
    """
    monitor_index = int(SCREEN_STATE["monitor_index"])

    with mss.mss() as screen_capture:
        monitors = screen_capture.monitors
        if monitor_index < 0 or monitor_index >= len(monitors):
            raise ValueError(
                f"Monitor index {monitor_index} is unavailable. "
                f"Choose an index from 0 to {len(monitors) - 1}."
            )

        monitor = monitors[monitor_index]
        screenshot = screen_capture.grab(monitor)

    image = Image.frombytes("RGB", screenshot.size, screenshot.rgb)
    original_width, original_height = image.size

    if SHOW_CURSOR_IN_SCREEN_STREAM:
        try:
            cursor = pyautogui.position()
            local_x = int(cursor.x) - int(monitor["left"])
            local_y = int(cursor.y) - int(monitor["top"])
            if 0 <= local_x < original_width and 0 <= local_y < original_height:
                draw = ImageDraw.Draw(image)
                radius = CURSOR_MARKER_RADIUS
                draw.ellipse(
                    (
                        local_x - radius,
                        local_y - radius,
                        local_x + radius,
                        local_y + radius,
                    ),
                    outline="red",
                    width=3,
                )
                draw.line(
                    (local_x - radius, local_y, local_x + radius, local_y),
                    fill="red",
                    width=2,
                )
                draw.line(
                    (local_x, local_y - radius, local_x, local_y + radius),
                    fill="red",
                    width=2,
                )
        except Exception:
            # Screen streaming should continue even if cursor lookup fails.
            pass

    if max(image.size) > SCREEN_MAX_DIMENSION:
        image.thumbnail(
            (SCREEN_MAX_DIMENSION, SCREEN_MAX_DIMENSION),
            Image.Resampling.LANCZOS,
        )

    metadata = {
        "monitor_index": monitor_index,
        "original_width": original_width,
        "original_height": original_height,
    }
    return image, metadata


def _encode_screen_image(image: Image.Image, metadata: dict[str, Any]) -> tuple[bytes, dict[str, Any]]:
    output = BytesIO()
    image.save(output, format="JPEG", quality=SCREEN_JPEG_QUALITY, optimize=True)
    frame = output.getvalue()
    return frame, {
        **metadata,
        "sent_width": image.width,
        "sent_height": image.height,
        "jpeg_bytes": len(frame),
    }


def capture_screen_frame() -> tuple[bytes, dict[str, Any]]:
    """Capture the selected Windows monitor and return JPEG bytes + metadata."""
    image, metadata = capture_screen_image()
    if SHOW_COORDINATE_GRID:
        draw_coordinate_grid(image)
    return _encode_screen_image(image, metadata)


def capture_combined_frame(camera_image: Image.Image) -> tuple[bytes, dict[str, Any]]:
    """Like capture_screen_frame, but pastes `camera_image` as a
    picture-in-picture thumbnail in the bottom-right corner first. Used when
    screen and camera vision are both enabled, so only one video frame (not
    two unrelated ones) is sent to Gemini Live per tick.
    """
    from config import CAMERA_PIP_MARGIN, CAMERA_PIP_MAX_WIDTH

    image, metadata = capture_screen_image()

    thumbnail = camera_image.copy()
    if thumbnail.width > CAMERA_PIP_MAX_WIDTH:
        scale = CAMERA_PIP_MAX_WIDTH / thumbnail.width
        thumbnail = thumbnail.resize(
            (CAMERA_PIP_MAX_WIDTH, max(1, int(thumbnail.height * scale))),
            Image.Resampling.LANCZOS,
        )

    x = image.width - thumbnail.width - CAMERA_PIP_MARGIN
    y = image.height - thumbnail.height - CAMERA_PIP_MARGIN
    if x >= 0 and y >= 0:
        draw = ImageDraw.Draw(image)
        draw.rectangle(
            (x - 2, y - 2, x + thumbnail.width + 2, y + thumbnail.height + 2),
            outline=(255, 255, 255),
            width=2,
        )
        image.paste(thumbnail, (x, y))

    if SHOW_COORDINATE_GRID:
        draw_coordinate_grid(image)

    return _encode_screen_image(image, metadata)
