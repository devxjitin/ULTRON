"""Webcam capture: camera enumeration, frame capture, and handle lifecycle."""

from camera.capture import (
    capture_camera_frame,
    get_available_cameras,
    get_camera_capture_status,
    release_camera_handle,
    set_camera_capture,
)

__all__ = [
    "capture_camera_frame",
    "get_available_cameras",
    "get_camera_capture_status",
    "release_camera_handle",
    "set_camera_capture",
]
