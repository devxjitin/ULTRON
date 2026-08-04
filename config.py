"""Shared configuration, tunables, and runtime state for Ultron."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pyaudio

# =========================================================
# AI MODELS
# =========================================================

MODEL = "gemini-3.1-flash-live-preview"
VOICE_NAME = "Algieba"

# The Live API caps how long a single WebSocket session may stay open; the
# server closes it with a GoAway message shortly before that limit and the
# client is expected to reconnect (optionally resuming context via
# session_resumption). Without handling that, the process used to crash with
# "APIError: 1008 ... GoAway signal ..." once the session got old enough.
SESSION_RECONNECT_BASE_DELAY_SECONDS = 2.0
SESSION_RECONNECT_MAX_DELAY_SECONDS = 30.0

# The live voice model above is unreliable at pixel-level pointing (clicks
# land "close but not exact" or far off). A separate, non-live model is used
# only to answer "where on screen is X" questions with normalized 0-1000
# coordinates; the live model then just describes the target in words.
COORDINATE_MODEL = "gemini-3.1-flash-lite"
COORDINATE_MODEL_MAX_DIMENSION = 1568
COORDINATE_MODEL_JPEG_QUALITY = 90

# =========================================================
# AUDIO
# =========================================================

FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE_IN = 16_000
RATE_OUT = 24_000
CHUNK = 512

# Digital gain applied to microphone input before it is sent, on top of the
# server-side VAD sensitivity tuning (see automatic_activity_detection in
# voice/assistant.py). Many mics/rooms produce a signal that's simply quiet
# at the source; boosting it here helps regardless of VAD settings. 1.0 = no
# change; clipped to the valid 16-bit range to avoid digital distortion.
MIC_GAIN = 2.0

# Smaller playback pieces make spoken interruption stop more quickly.
PLAYBACK_CHUNK_MS = 40
BYTES_PER_AUDIO_SAMPLE = 2  # paInt16
PLAYBACK_CHUNK_BYTES = int(
    RATE_OUT * CHANNELS * BYTES_PER_AUDIO_SAMPLE * PLAYBACK_CHUNK_MS / 1000
)

# =========================================================
# PATHS
# =========================================================

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_WORKING_DIRECTORY = Path.cwd().resolve()
MEMORY_DB = BASE_DIR / "voice_assistant_memory.db"

# =========================================================
# TOOL / MEMORY LIMITS
# =========================================================

DEFAULT_COMMAND_TIMEOUT_SECONDS = 120
MAX_COMMAND_TIMEOUT_SECONDS = 3_600
MAX_TOOL_OUTPUT_CHARACTERS = 30_000
MAX_RECENT_MEMORIES_IN_PROMPT = 10
MAX_RECENT_TURNS_IN_PROMPT = 10

# =========================================================
# SCREEN CAPTURE
# =========================================================

# The realtime API supports video as image frames at a maximum of 1 FPS. Both
# screen and camera vision are on by default (see
# CAMERA_CAPTURE_ENABLED_AT_START below) -- Ultron should be able to see
# through both the moment it starts, without needing a voice command first.
# When both are enabled they share that single 1 FPS budget: the camera is
# composited into the screen frame as a small picture-in-picture thumbnail
# rather than sent as a second, unrelated video source (see
# CAMERA_PIP_MAX_WIDTH below and voice/assistant.py's send_video_frames).
SCREEN_CAPTURE_ENABLED_AT_START = True
SCREEN_MONITOR_INDEX = 1  # 1 = primary monitor, 0 = all monitors combined
SCREEN_FPS = 1.0
SCREEN_MAX_DIMENSION = 1_600
SCREEN_JPEG_QUALITY = 80
SCREEN_CAPTURE_RETRY_SECONDS = 2.0
SCREEN_ERROR_LOG_INTERVAL_SECONDS = 10.0
SHOW_CURSOR_IN_SCREEN_STREAM = True
CURSOR_MARKER_RADIUS = 12

# Coordinate-accuracy fix: draw a light reference grid with normalized
# (0-1000) coordinate labels over the streamed frame. Without this, the
# model has to eyeball a target's position on a 0-1000 scale purely from
# pixels, which is why clicks land "close but not exact" or sometimes far
# off. With labeled gridlines the model can read the nearest intersection
# and offset from it, which is far more precise and consistent.
SHOW_COORDINATE_GRID = True
GRID_STEP = 100  # one gridline every 100 normalized units (10x10 grid)
GRID_LINE_COLOR = (0, 200, 255)
GRID_LABEL_COLOR = (0, 200, 255)

# =========================================================
# CAMERA CAPTURE
# =========================================================

# On by default: Ultron should be able to see through the webcam the moment
# it starts, without needing a voice command first.
CAMERA_CAPTURE_ENABLED_AT_START = True
CAMERA_INDEX = 0
CAMERA_FPS = 1.0
CAMERA_MAX_DIMENSION = 1_024
CAMERA_JPEG_QUALITY = 80
CAMERA_CAPTURE_RETRY_SECONDS = 2.0
CAMERA_ERROR_LOG_INTERVAL_SECONDS = 10.0
CAMERA_PROBE_INDEX_COUNT = 4  # how many device indexes to check for availability

# Size (in pixels, width) of the camera picture-in-picture thumbnail pasted
# into the bottom-right corner of the screen frame when both screen and
# camera vision are enabled at once.
CAMERA_PIP_MAX_WIDTH = 320
CAMERA_PIP_MARGIN = 16

# =========================================================
# CONTINUOUS TASK MODE
# =========================================================

# Explicit, user-started autonomy: when the user asks Ultron to keep doing
# something on its own ("keep replying to them on WhatsApp, whatever they
# say"), it calls start_continuous_task and then gets a nudge every
# CONTINUOUS_TASK_POLL_SECONDS to check on and continue that task, without
# needing the user to re-prompt it each time. Runs only while a task is
# active, and only until stop_continuous_task is called (task finished, user
# said stop, or nothing new to act on). Unlike a background/idle trigger,
# this never starts itself — the user has to explicitly ask for it.
CONTINUOUS_TASK_POLL_SECONDS = 5.0

# =========================================================
# GUI AUTOMATION
# =========================================================

# GUI automation behavior. Move the pointer to a screen corner to trigger the
# PyAutoGUI emergency fail-safe. Ctrl+C in the console also stops the program.
PYAUTOGUI_FAILSAFE = True
PYAUTOGUI_PAUSE_SECONDS = 0.05
POST_GUI_ACTION_SETTLE_SECONDS = 0.25
MAX_TYPED_TEXT_CHARACTERS = 20_000
MAX_KEY_PRESSES = 100
MAX_MOUSE_CLICKS = 10
MAX_SCROLL_CLICKS = 100
MAX_GUI_DURATION_SECONDS = 30.0
MAX_WAIT_SECONDS = 30.0

# On Windows, pyautogui.scroll(n)/hscroll(n) pass n straight through as the
# raw WM_MOUSEWHEEL delta with no scaling. A real physical wheel notch is
# WHEEL_DELTA=120 of that same unit, so passing a human-sized "n=3 clicks"
# straight through moves the content by 3/120th of one notch — imperceptible.
# scroll_mouse multiplies by this before calling pyautogui so "clicks" in the
# tool's API actually means real wheel notches.
SCROLL_WHEEL_DELTA = 120

MAX_APPLICATION_LAUNCH_TARGET_CHARACTERS = 500

# =========================================================
# RUNTIME STATE
# =========================================================

SCREEN_STATE: dict[str, Any] = {
    "enabled": SCREEN_CAPTURE_ENABLED_AT_START,
    "monitor_index": SCREEN_MONITOR_INDEX,
    "last_frame_at": None,
    "last_frame_width": None,
    "last_frame_height": None,
    "last_frame_bytes": None,
    "last_error": None,
}

# Screen vision and camera vision share the single realtime video input.
# Both can be "enabled" at once -- see send_video_frames in
# voice/assistant.py, which composites the camera as a picture-in-picture
# thumbnail onto the screen frame when that happens, instead of trying to
# send two unrelated video sources.
CAMERA_STATE: dict[str, Any] = {
    "enabled": CAMERA_CAPTURE_ENABLED_AT_START,
    "camera_index": CAMERA_INDEX,
    "last_frame_at": None,
    "last_frame_width": None,
    "last_frame_height": None,
    "last_frame_bytes": None,
    "last_error": None,
}

# Tool-call IDs can occasionally be repeated by a streaming session.
PROCESSED_TOOL_CALL_CACHE_SIZE = 200
processed_tool_calls: dict[str, dict[str, Any]] = {}

CONTINUOUS_TASK_STATE: dict[str, Any] = {
    "active": False,
    "description": "",
}
