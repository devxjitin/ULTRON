"""Webcam capture used to give Ultron webcam vision (no GUI/preview window).

Frames are grabbed with OpenCV and JPEG-encoded the same way screen frames
are, then streamed to Gemini Live as ordinary video input. There is no
on-screen preview: capture happens headlessly in the background exactly
like screen sharing does.
"""

from __future__ import annotations

import os
import threading
from io import BytesIO
from typing import Any

import cv2
from PIL import Image

from config import (
    CAMERA_JPEG_QUALITY,
    CAMERA_MAX_DIMENSION,
    CAMERA_PROBE_INDEX_COUNT,
    CAMERA_STATE,
    SCREEN_STATE,
)
from screen.capture import coerce_bool

# A cv2.VideoCapture handle takes real time (100ms-2s) to open a device, so
# unlike screen frames (cheap to grab fresh every time via mss) the webcam
# handle is opened once and reused across frames, and released whenever
# capture is disabled or the process exits so the camera light turns off.
_camera_lock = threading.Lock()
_camera_handle: cv2.VideoCapture | None = None
_camera_handle_index: int | None = None


def _capture_backend() -> int:
    # DirectShow opens noticeably faster and more reliably than the default
    # Media Foundation backend on most Windows webcams.
    return cv2.CAP_DSHOW if os.name == "nt" else cv2.CAP_ANY


def _open_camera_handle(camera_index: int) -> cv2.VideoCapture:
    handle = cv2.VideoCapture(camera_index, _capture_backend())
    if not handle.isOpened():
        handle.release()
        raise RuntimeError(
            f"Unable to open camera index {camera_index}. It may be in use "
            "by another application or not present."
        )
    return handle


def _get_camera_handle(camera_index: int) -> cv2.VideoCapture:
    global _camera_handle, _camera_handle_index

    with _camera_lock:
        if (
            _camera_handle is not None
            and _camera_handle_index == camera_index
            and _camera_handle.isOpened()
        ):
            return _camera_handle

        if _camera_handle is not None:
            _camera_handle.release()
            _camera_handle = None
            _camera_handle_index = None

        _camera_handle = _open_camera_handle(camera_index)
        _camera_handle_index = camera_index
        return _camera_handle


def release_camera_handle() -> None:
    """Release the webcam device, turning off its indicator light."""
    global _camera_handle, _camera_handle_index

    with _camera_lock:
        if _camera_handle is not None:
            _camera_handle.release()
        _camera_handle = None
        _camera_handle_index = None


def get_available_cameras(probe_count: int = CAMERA_PROBE_INDEX_COUNT) -> list[int]:
    """Probe the first few device indexes and return which ones open."""
    available: list[int] = []
    for index in range(max(1, probe_count)):
        if index == _camera_handle_index and _camera_handle is not None:
            available.append(index)
            continue
        probe = cv2.VideoCapture(index, _capture_backend())
        try:
            if probe.isOpened():
                available.append(index)
        finally:
            probe.release()
    return available


def set_camera_capture(
    enabled: bool,
    camera_index: int | None = None,
) -> dict[str, Any]:
    """Enable/disable webcam vision and optionally select a camera device."""
    is_enabled = coerce_bool(enabled)
    target_index = (
        int(camera_index) if camera_index is not None else CAMERA_STATE["camera_index"]
    )

    if is_enabled:
        # Fail fast with a clear error instead of discovering on the next
        # streamed frame, and open the device now so the indicator light
        # reflects the change immediately.
        _get_camera_handle(target_index)
        CAMERA_STATE["camera_index"] = target_index
        CAMERA_STATE["enabled"] = True
        CAMERA_STATE["last_error"] = None
        # See the matching note in screen.capture.set_screen_capture: only
        # one video source is streamed to Gemini Live at a time.
        SCREEN_STATE["enabled"] = False
    else:
        CAMERA_STATE["enabled"] = False
        release_camera_handle()

    return {
        "status": "completed",
        "camera_capture_enabled": CAMERA_STATE["enabled"],
        "camera_index": CAMERA_STATE["camera_index"],
    }


def get_camera_capture_status() -> dict[str, Any]:
    try:
        available_cameras = get_available_cameras()
    except Exception as error:
        available_cameras = []
        CAMERA_STATE["last_error"] = f"{type(error).__name__}: {error}"

    return {
        "status": "completed",
        "camera_capture_enabled": CAMERA_STATE["enabled"],
        "camera_index": CAMERA_STATE["camera_index"],
        "last_frame_at": CAMERA_STATE["last_frame_at"],
        "last_frame_width": CAMERA_STATE["last_frame_width"],
        "last_frame_height": CAMERA_STATE["last_frame_height"],
        "last_frame_bytes": CAMERA_STATE["last_frame_bytes"],
        "last_error": CAMERA_STATE["last_error"],
        "available_cameras": available_cameras,
    }


def capture_camera_frame() -> tuple[bytes, dict[str, Any]]:
    """Grab one webcam frame and return JPEG bytes + metadata."""
    camera_index = int(CAMERA_STATE["camera_index"])
    handle = _get_camera_handle(camera_index)

    with _camera_lock:
        ok, frame = handle.read()
    if not ok or frame is None:
        raise RuntimeError(f"Failed to read a frame from camera index {camera_index}.")

    # OpenCV frames are BGR; PIL/JPEG expect RGB.
    image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    original_width, original_height = image.size

    if max(image.size) > CAMERA_MAX_DIMENSION:
        image.thumbnail(
            (CAMERA_MAX_DIMENSION, CAMERA_MAX_DIMENSION),
            Image.Resampling.LANCZOS,
        )

    output = BytesIO()
    image.save(output, format="JPEG", quality=CAMERA_JPEG_QUALITY, optimize=True)
    encoded = output.getvalue()

    metadata = {
        "camera_index": camera_index,
        "original_width": original_width,
        "original_height": original_height,
        "sent_width": image.width,
        "sent_height": image.height,
        "jpeg_bytes": len(encoded),
    }
    return encoded, metadata
