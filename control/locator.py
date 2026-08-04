"""Description-based pointing: locate on-screen targets with a dedicated model."""

from __future__ import annotations

import json
import os
from io import BytesIO
from typing import Any

import mss
from PIL import Image
from google import genai
from google.genai import types

from config import COORDINATE_MODEL, COORDINATE_MODEL_JPEG_QUALITY, COORDINATE_MODEL_MAX_DIMENSION
from control.mouse_keyboard import (
    _normalized_coordinate,
    _selected_monitor,
    click_mouse,
    drag_mouse,
    move_mouse,
)

_LOCATOR_CLIENT: genai.Client | None = None


def _get_locator_client() -> genai.Client:
    """Lazily create the client used only for coordinate lookups.

    Kept separate from the Live session's client so this works independently
    of the voice session's lifecycle.
    """
    global _LOCATOR_CLIENT
    if _LOCATOR_CLIENT is None:
        api_key = os.getenv("ASSISTANT_API_KEY")
        if not api_key:
            raise RuntimeError("Set ASSISTANT_API_KEY before running the program.")
        _LOCATOR_CLIENT = genai.Client(api_key=api_key)
    return _LOCATOR_CLIENT


def _capture_locator_frame() -> tuple[bytes, dict[str, int]]:
    """Grab a fresh, high-resolution screenshot of the selected monitor.

    This is captured independently of the lower-resolution, grid-annotated
    frame streamed to the live voice model, so the locator model gets the
    clearest possible view for precise pointing.
    """
    monitor = _selected_monitor()
    with mss.mss() as screen_capture:
        screenshot = screen_capture.grab(
            {
                "left": monitor["left"],
                "top": monitor["top"],
                "width": monitor["width"],
                "height": monitor["height"],
            }
        )

    image = Image.frombytes("RGB", screenshot.size, screenshot.rgb)
    if max(image.size) > COORDINATE_MODEL_MAX_DIMENSION:
        image.thumbnail(
            (COORDINATE_MODEL_MAX_DIMENSION, COORDINATE_MODEL_MAX_DIMENSION),
            Image.Resampling.LANCZOS,
        )

    output = BytesIO()
    image.save(output, format="JPEG", quality=COORDINATE_MODEL_JPEG_QUALITY)
    return output.getvalue(), {"width": image.width, "height": image.height}


def _parse_locator_json(raw_text: str) -> dict[str, Any]:
    cleaned = raw_text.strip().strip("`").strip()
    if cleaned.lower().startswith("json"):
        cleaned = cleaned[4:].strip()

    try:
        return json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError(
                f"Locator model returned unparsable output: {raw_text!r}"
            )
        return json.loads(cleaned[start : end + 1])


def locate_target_on_screen(description: str) -> dict[str, Any]:
    """Ask the dedicated coordinate model for a target's precise location.

    Returns normalized 0-1000 x/y coordinates for the center of the
    described on-screen element, using COORDINATE_MODEL instead of the live
    voice model, which is not reliable at this kind of pointing task.
    """
    text = str(description or "").strip()
    if not text:
        raise ValueError("description cannot be empty.")

    frame_bytes, frame_size = _capture_locator_frame()
    client = _get_locator_client()

    prompt = (
        "This is a screenshot of a computer screen. Find this UI target: "
        f'"{text}".\n'
        "Respond with ONLY a compact JSON object, no other text, of the form "
        '{"x": <number>, "y": <number>, "found": true or false}. '
        "x and y are the normalized coordinates of the CENTER of the target, "
        "on a scale from 0 (left/top edge of the image) to 1000 (right/bottom "
        "edge). If you cannot find the target, set found to false and still "
        "give your single best-guess x/y."
    )

    response = client.models.generate_content(
        model=COORDINATE_MODEL,
        contents=[
            types.Part.from_bytes(data=frame_bytes, mime_type="image/jpeg"),
            prompt,
        ],
    )

    parsed = _parse_locator_json(response.text or "")
    normalized_x = _normalized_coordinate(parsed.get("x"), "x")
    normalized_y = _normalized_coordinate(parsed.get("y"), "y")

    return {
        "status": "completed",
        "description": text,
        "found": bool(parsed.get("found", True)),
        "x": normalized_x,
        "y": normalized_y,
        "frame_size": frame_size,
    }


def click_on_description(
    description: str,
    button: Any = "left",
    clicks: Any = 1,
    interval_seconds: Any = 0.12,
) -> dict[str, Any]:
    """Locate a described target with COORDINATE_MODEL, then click it."""
    located = locate_target_on_screen(description)
    result = click_mouse(located["x"], located["y"], button, clicks, interval_seconds)
    result["located"] = located
    return result


def move_to_description(
    description: str,
    duration_seconds: Any = 0.2,
) -> dict[str, Any]:
    """Locate a described target with COORDINATE_MODEL, then move to it."""
    located = locate_target_on_screen(description)
    result = move_mouse(located["x"], located["y"], duration_seconds)
    result["located"] = located
    return result


def drag_between_descriptions(
    start_description: str,
    end_description: str,
    duration_seconds: Any = 0.7,
    button: Any = "left",
) -> dict[str, Any]:
    """Locate two described targets with COORDINATE_MODEL, then drag between them."""
    start_located = locate_target_on_screen(start_description)
    end_located = locate_target_on_screen(end_description)
    result = drag_mouse(
        start_located["x"],
        start_located["y"],
        end_located["x"],
        end_located["y"],
        duration_seconds,
        button,
    )
    result["start_located"] = start_located
    result["end_located"] = end_located
    return result
