"""
Gemini Live voice assistant for Windows with:
- Continuous two-way voice conversation with voice barge-in interruption
- Dedicated CMD and PowerShell tools
- Mouse movement, click, double-click, drag, and scrolling
- Keyboard typing, key presses, and hotkeys
- Persistent SQLite long-term memory
- Automatic conversation transcript storage
- Continuous Windows screen vision streamed to Gemini Live
- Voice-controlled screen-sharing enable/disable and monitor selection

Install dependencies:
    pip install -U google-genai pyaudio mss pillow pyautogui pyperclip

Set your API key before running:
    set GEMINI_API_KEY=your_api_key_here

WARNING:
This program gives the model direct access to your Windows shell, mouse, and
keyboard with the same permissions as the Python process. Run it only in a
disposable test account, Windows Sandbox, or VM. Do not run it as Administrator.
Move the mouse rapidly to a screen corner or press Ctrl+C in the console to stop.
"""

from __future__ import annotations

import asyncio
import ctypes
import json
import locale
import os
import time
from io import BytesIO
import shutil
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import mss
import pyaudio
import pyautogui
import pyperclip
from PIL import Image, ImageDraw
from google import genai
from google.genai import types


# =========================================================
# CONFIGURATION
# =========================================================

MODEL = "gemini-3.1-flash-live-preview"
VOICE_NAME = "Kore"

# The live voice model above is unreliable at pixel-level pointing (clicks
# land "close but not exact" or far off). A separate, non-live model is used
# only to answer "where on screen is X" questions with normalized 0-1000
# coordinates; the live model then just describes the target in words.
COORDINATE_MODEL = "gemini-3.1-flash-lite"
COORDINATE_MODEL_MAX_DIMENSION = 1568
COORDINATE_MODEL_JPEG_QUALITY = 90

FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE_IN = 16_000
RATE_OUT = 24_000
CHUNK = 512

# Smaller playback pieces make spoken interruption stop more quickly.
PLAYBACK_CHUNK_MS = 40
BYTES_PER_AUDIO_SAMPLE = 2  # paInt16
PLAYBACK_CHUNK_BYTES = int(
    RATE_OUT * CHANNELS * BYTES_PER_AUDIO_SAMPLE * PLAYBACK_CHUNK_MS / 1000
)

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_WORKING_DIRECTORY = Path.cwd().resolve()
MEMORY_DB = BASE_DIR / "voice_assistant_memory.db"

DEFAULT_COMMAND_TIMEOUT_SECONDS = 120
MAX_COMMAND_TIMEOUT_SECONDS = 3_600
MAX_TOOL_OUTPUT_CHARACTERS = 30_000
MAX_RECENT_MEMORIES_IN_PROMPT = 10
MAX_RECENT_TURNS_IN_PROMPT = 10

# Gemini Live supports video as image frames at a maximum of 1 FPS.
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

SCREEN_STATE: dict[str, Any] = {
    "enabled": SCREEN_CAPTURE_ENABLED_AT_START,
    "monitor_index": SCREEN_MONITOR_INDEX,
    "last_frame_at": None,
    "last_frame_width": None,
    "last_frame_height": None,
    "last_frame_bytes": None,
    "last_error": None,
}

# Tool-call IDs can occasionally be repeated by a streaming session.
PROCESSED_TOOL_CALL_CACHE_SIZE = 200
processed_tool_calls: dict[str, dict[str, Any]] = {}


# =========================================================
# SQLITE MEMORY
# =========================================================


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_db() -> sqlite3.Connection:
    connection = sqlite3.connect(MEMORY_DB, timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_memory_database() -> None:
    with get_db() as db:
        db.executescript(
            """
            PRAGMA journal_mode=WAL;

            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                memory_key TEXT NOT NULL,
                memory_value TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'general',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(memory_key, category)
            );

            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_text TEXT NOT NULL DEFAULT '',
                assistant_text TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS terminal_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                shell TEXT NOT NULL,
                command TEXT NOT NULL,
                working_directory TEXT NOT NULL,
                exit_code INTEGER,
                stdout TEXT NOT NULL DEFAULT '',
                stderr TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_memories_updated
                ON memories(updated_at DESC);

            CREATE INDEX IF NOT EXISTS idx_conversations_created
                ON conversations(created_at DESC);

            CREATE INDEX IF NOT EXISTS idx_terminal_created
                ON terminal_history(created_at DESC);
            """
        )

        # Full-text index for relevance-ranked memory search. LIKE-based
        # search only matched literal substrings and returned results in
        # recency order regardless of relevance, so weakly-related memories
        # often got fed into the prompt ahead of the actually relevant one,
        # which is a common cause of the model mixing up or inventing facts.
        # FTS5 lets us rank by real textual relevance (bm25) instead.
        try:
            db.executescript(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
                    memory_key,
                    memory_value,
                    category,
                    content='memories',
                    content_rowid='id'
                );

                CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
                    INSERT INTO memories_fts(rowid, memory_key, memory_value, category)
                    VALUES (new.id, new.memory_key, new.memory_value, new.category);
                END;

                CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
                    INSERT INTO memories_fts(memories_fts, rowid, memory_key, memory_value, category)
                    VALUES ('delete', old.id, old.memory_key, old.memory_value, old.category);
                END;

                CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
                    INSERT INTO memories_fts(memories_fts, rowid, memory_key, memory_value, category)
                    VALUES ('delete', old.id, old.memory_key, old.memory_value, old.category);
                    INSERT INTO memories_fts(rowid, memory_key, memory_value, category)
                    VALUES (new.id, new.memory_key, new.memory_value, new.category);
                END;
                """
            )
            # Backfill the FTS index for any rows that predate the triggers
            # (e.g. an existing database from before this upgrade).
            db.execute(
                """
                INSERT INTO memories_fts(rowid, memory_key, memory_value, category)
                SELECT m.id, m.memory_key, m.memory_value, m.category
                FROM memories AS m
                LEFT JOIN memories_fts AS f ON f.rowid = m.id
                WHERE f.rowid IS NULL
                """
            )
        except sqlite3.OperationalError:
            # FTS5 unavailable in this SQLite build; search_memory() falls
            # back to LIKE matching automatically.
            pass


def remember_memory(
    memory_key: str,
    memory_value: str,
    category: str = "general",
) -> dict[str, Any]:
    key = str(memory_key).strip()
    value = str(memory_value).strip()
    category = str(category or "general").strip() or "general"

    if not key:
        raise ValueError("memory_key cannot be empty.")
    if not value:
        raise ValueError("memory_value cannot be empty.")

    timestamp = utc_now()

    with get_db() as db:
        db.execute(
            """
            INSERT INTO memories(
                memory_key, memory_value, category, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(memory_key, category) DO UPDATE SET
                memory_value = excluded.memory_value,
                updated_at = excluded.updated_at
            """,
            (key, value, category, timestamp, timestamp),
        )

        row = db.execute(
            """
            SELECT id, memory_key, memory_value, category, created_at, updated_at
            FROM memories
            WHERE memory_key = ? AND category = ?
            """,
            (key, category),
        ).fetchone()

    return {
        "status": "saved",
        "memory": dict(row) if row else None,
        "database": str(MEMORY_DB),
    }


def _fts_match_expression(text: str) -> str:
    """Build a safe FTS5 MATCH expression (OR of quoted tokens)."""
    tokens = [token for token in text.replace('"', " ").split() if token]
    if not tokens:
        return ""
    return " OR ".join(f'"{token}"' for token in tokens)


def search_memory(query: str, limit: int = 10) -> dict[str, Any]:
    text = str(query).strip()
    safe_limit = max(1, min(int(limit or 10), 100))

    if not text:
        return list_memories(safe_limit)

    match_expression = _fts_match_expression(text)
    with get_db() as db:
        rows: list[sqlite3.Row] = []
        if match_expression:
            try:
                rows = db.execute(
                    """
                    SELECT m.id, m.memory_key, m.memory_value, m.category,
                           m.created_at, m.updated_at
                    FROM memories_fts AS f
                    JOIN memories AS m ON m.id = f.rowid
                    WHERE memories_fts MATCH ?
                    ORDER BY bm25(memories_fts), m.updated_at DESC
                    LIMIT ?
                    """,
                    (match_expression, safe_limit),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []

        if not rows:
            # Fall back to substring matching so results are never worse
            # than before, just re-ranked when FTS is available.
            pattern = f"%{text}%"
            rows = db.execute(
                """
                SELECT id, memory_key, memory_value, category, created_at, updated_at
                FROM memories
                WHERE memory_key LIKE ? COLLATE NOCASE
                   OR memory_value LIKE ? COLLATE NOCASE
                   OR category LIKE ? COLLATE NOCASE
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (pattern, pattern, pattern, safe_limit),
            ).fetchall()

    return {
        "status": "completed",
        "query": text,
        "count": len(rows),
        "memories": [dict(row) for row in rows],
    }


def list_memories(limit: int = 20) -> dict[str, Any]:
    safe_limit = max(1, min(int(limit or 20), 100))

    with get_db() as db:
        rows = db.execute(
            """
            SELECT id, memory_key, memory_value, category, created_at, updated_at
            FROM memories
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (safe_limit,),
        ).fetchall()

    return {
        "status": "completed",
        "count": len(rows),
        "memories": [dict(row) for row in rows],
    }


def forget_memory(
    memory_id: int | None = None,
    query: str = "",
) -> dict[str, Any]:
    deleted = 0

    with get_db() as db:
        if memory_id is not None:
            cursor = db.execute(
                "DELETE FROM memories WHERE id = ?",
                (int(memory_id),),
            )
            deleted = cursor.rowcount
        elif str(query).strip():
            pattern = f"%{str(query).strip()}%"
            cursor = db.execute(
                """
                DELETE FROM memories
                WHERE memory_key LIKE ? COLLATE NOCASE
                   OR memory_value LIKE ? COLLATE NOCASE
                   OR category LIKE ? COLLATE NOCASE
                """,
                (pattern, pattern, pattern),
            )
            deleted = cursor.rowcount
        else:
            raise ValueError("Provide memory_id or query.")

    return {"status": "completed", "deleted": deleted}


def save_conversation(user_text: str, assistant_text: str) -> None:
    user_text = str(user_text).strip()
    assistant_text = str(assistant_text).strip()

    if not user_text and not assistant_text:
        return

    with get_db() as db:
        db.execute(
            """
            INSERT INTO conversations(user_text, assistant_text, created_at)
            VALUES (?, ?, ?)
            """,
            (user_text, assistant_text, utc_now()),
        )


def recall_recent_conversation(limit: int = 10) -> dict[str, Any]:
    safe_limit = max(1, min(int(limit or 10), 50))

    with get_db() as db:
        rows = db.execute(
            """
            SELECT id, user_text, assistant_text, created_at
            FROM conversations
            ORDER BY id DESC
            LIMIT ?
            """,
            (safe_limit,),
        ).fetchall()

    ordered_rows = list(reversed(rows))
    return {
        "status": "completed",
        "count": len(ordered_rows),
        "turns": [dict(row) for row in ordered_rows],
    }


def save_terminal_history(result: dict[str, Any]) -> None:
    with get_db() as db:
        db.execute(
            """
            INSERT INTO terminal_history(
                shell, command, working_directory, exit_code,
                stdout, stderr, status, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(result.get("shell", "")),
                str(result.get("command", "")),
                str(result.get("working_directory", "")),
                result.get("exit_code"),
                str(result.get("stdout", "")),
                str(result.get("stderr", "")),
                str(result.get("status", "unknown")),
                utc_now(),
            ),
        )


def recent_terminal_history(limit: int = 10) -> dict[str, Any]:
    safe_limit = max(1, min(int(limit or 10), 50))

    with get_db() as db:
        rows = db.execute(
            """
            SELECT id, shell, command, working_directory, exit_code,
                   stdout, stderr, status, created_at
            FROM terminal_history
            ORDER BY id DESC
            LIMIT ?
            """,
            (safe_limit,),
        ).fetchall()

    return {
        "status": "completed",
        "count": len(rows),
        "commands": [dict(row) for row in rows],
    }


def build_memory_context() -> str:
    memories = list_memories(MAX_RECENT_MEMORIES_IN_PROMPT)["memories"]
    conversations = recall_recent_conversation(MAX_RECENT_TURNS_IN_PROMPT)["turns"]

    sections: list[str] = []

    if memories:
        lines = [
            f"- [{item['category']}] {item['memory_key']}: {item['memory_value']}"
            for item in memories
        ]
        sections.append("Persistent memories:\n" + "\n".join(lines))

    if conversations:
        lines = []
        for turn in conversations:
            if turn["user_text"]:
                lines.append(f"User: {turn['user_text']}")
            if turn["assistant_text"]:
                lines.append(f"Assistant: {turn['assistant_text']}")
        sections.append("Recent conversation transcript:\n" + "\n".join(lines))

    return "\n\n".join(sections) or "No persistent memory exists yet."


# =========================================================
# DEDICATED CMD AND POWERSHELL EXECUTION
# =========================================================


def decode_output(data: bytes) -> str:
    if not data:
        return ""

    preferred = locale.getpreferredencoding(False) or "utf-8"
    encodings = ["utf-8", preferred, "utf-16-le", "cp1252"]

    for encoding in dict.fromkeys(encodings):
        try:
            text = data.decode(encoding)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    else:
        text = data.decode("utf-8", errors="replace")

    text = text.replace("\x00", "").strip()
    if len(text) > MAX_TOOL_OUTPUT_CHARACTERS:
        text = (
            text[:MAX_TOOL_OUTPUT_CHARACTERS]
            + "\n\n[Output truncated by the voice assistant]"
        )
    return text


def resolve_working_directory(value: str | None) -> Path:
    if not value or not str(value).strip():
        return DEFAULT_WORKING_DIRECTORY

    path = Path(os.path.expandvars(os.path.expanduser(str(value).strip())))
    if not path.is_absolute():
        path = DEFAULT_WORKING_DIRECTORY / path

    path = path.resolve()
    if not path.exists():
        raise FileNotFoundError(f"Working directory does not exist: {path}")
    if not path.is_dir():
        raise NotADirectoryError(f"Not a directory: {path}")
    return path


async def _run_shell_command(
    shell: str,
    command: str,
    working_directory: str = "",
    timeout_seconds: int = DEFAULT_COMMAND_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Internal process runner used by the separate CMD and PowerShell tools."""
    if os.name != "nt":
        return {
            "status": "error",
            "message": "The terminal tools are designed for Windows.",
        }

    command = str(command).strip()
    if not command:
        raise ValueError("command cannot be empty.")

    cwd = resolve_working_directory(working_directory)
    timeout = int(timeout_seconds or DEFAULT_COMMAND_TIMEOUT_SECONDS)
    timeout = max(1, min(timeout, MAX_COMMAND_TIMEOUT_SECONDS))

    if shell == "cmd":
        executable = os.environ.get("COMSPEC") or shutil.which("cmd.exe")
        if not executable:
            raise FileNotFoundError("cmd.exe was not found.")
        process_args = [executable, "/d", "/s", "/c", command]
    elif shell == "powershell":
        executable = shutil.which("pwsh.exe") or shutil.which("powershell.exe")
        if not executable:
            raise FileNotFoundError("PowerShell was not found.")

        powershell_command = (
            "$OutputEncoding=[Console]::OutputEncoding="
            "[System.Text.UTF8Encoding]::new(); "
            + command
        )
        process_args = [
            executable,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            powershell_command,
        ]
    else:
        raise ValueError(f"Unsupported shell: {shell}")

    print("\n" + "=" * 80)
    print(f"[EXECUTING WITH {shell.upper()}]")
    print(f"Working directory: {cwd}")
    print(f"Command: {command}")
    print("=" * 80)

    process = await asyncio.create_subprocess_exec(
        *process_args,
        cwd=str(cwd),
        env=os.environ.copy(),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout,
        )
        result = {
            "status": "completed",
            "shell": shell,
            "command": command,
            "working_directory": str(cwd),
            "timeout_seconds": timeout,
            "exit_code": process.returncode,
            "stdout": decode_output(stdout_bytes),
            "stderr": decode_output(stderr_bytes),
        }
    except asyncio.TimeoutError:
        process.kill()
        stdout_bytes, stderr_bytes = await process.communicate()
        result = {
            "status": "timeout",
            "shell": shell,
            "command": command,
            "working_directory": str(cwd),
            "timeout_seconds": timeout,
            "exit_code": process.returncode,
            "stdout": decode_output(stdout_bytes),
            "stderr": decode_output(stderr_bytes),
            "message": f"Command exceeded the {timeout}-second timeout.",
        }

    save_terminal_history(result)
    return result


async def run_cmd_command(
    command: str,
    working_directory: str = "",
    timeout_seconds: int = DEFAULT_COMMAND_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Execute a command through cmd.exe."""
    return await _run_shell_command(
        shell="cmd",
        command=command,
        working_directory=working_directory,
        timeout_seconds=timeout_seconds,
    )


async def run_powershell_command(
    command: str,
    working_directory: str = "",
    timeout_seconds: int = DEFAULT_COMMAND_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Execute a command through PowerShell/pwsh."""
    return await _run_shell_command(
        shell="powershell",
        command=command,
        working_directory=working_directory,
        timeout_seconds=timeout_seconds,
    )


# =========================================================
# WINDOWS SCREEN CAPTURE
# =========================================================


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


def capture_screen_frame() -> tuple[bytes, dict[str, Any]]:
    """Capture the selected Windows monitor and return JPEG bytes + metadata."""
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

    if SHOW_COORDINATE_GRID:
        draw_coordinate_grid(image)

    output = BytesIO()
    image.save(
        output,
        format="JPEG",
        quality=SCREEN_JPEG_QUALITY,
        optimize=True,
    )
    frame = output.getvalue()

    metadata = {
        "monitor_index": monitor_index,
        "original_width": original_width,
        "original_height": original_height,
        "sent_width": image.width,
        "sent_height": image.height,
        "jpeg_bytes": len(frame),
    }
    return frame, metadata


# =========================================================
# WINDOWS MOUSE AND KEYBOARD CONTROL
# =========================================================


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


_LOCATOR_CLIENT: genai.Client | None = None


def _get_locator_client() -> genai.Client:
    """Lazily create the client used only for coordinate lookups.

    Kept separate from the Live session's client so this works independently
    of the voice session's lifecycle.
    """
    global _LOCATOR_CLIENT
    if _LOCATOR_CLIENT is None:
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise RuntimeError(
                "Set GEMINI_API_KEY or GOOGLE_API_KEY before running the program."
            )
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
        "model": COORDINATE_MODEL,
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

    if vertical:
        pyautogui.scroll(vertical)
    if horizontal:
        pyautogui.hscroll(horizontal)

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


def wait_for_screen(seconds: Any = 1.0) -> dict[str, Any]:
    duration = max(0.0, min(float(seconds or 1.0), MAX_WAIT_SECONDS))
    time.sleep(duration)
    return {
        "status": "completed",
        "action": "wait_for_screen",
        "seconds": duration,
    }


# =========================================================
# GEMINI TOOL DECLARATIONS
# =========================================================

TOOLS = [
    {
        "function_declarations": [
            {
                "name": "run_cmd_command",
                "description": (
                    "Execute a command specifically through Windows cmd.exe. "
                    "Use this only when CMD syntax or behavior is appropriate."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "The exact complete CMD command.",
                        },
                        "working_directory": {
                            "type": "string",
                            "description": (
                                "Optional absolute or relative working directory."
                            ),
                        },
                        "timeout_seconds": {
                            "type": "integer",
                            "description": "Timeout from 1 to 3600 seconds.",
                        },
                    },
                    "required": ["command"],
                },
            },
            {
                "name": "run_powershell_command",
                "description": (
                    "Execute a command specifically through PowerShell or pwsh. "
                    "Prefer this for Windows management, object pipelines, and scripts."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "The exact complete PowerShell command.",
                        },
                        "working_directory": {
                            "type": "string",
                            "description": (
                                "Optional absolute or relative working directory."
                            ),
                        },
                        "timeout_seconds": {
                            "type": "integer",
                            "description": "Timeout from 1 to 3600 seconds.",
                        },
                    },
                    "required": ["command"],
                },
            },
            {
                "name": "get_computer_control_status",
                "description": (
                    "Return the selected monitor, coordinate system, current cursor "
                    "position, and emergency fail-safe information."
                ),
                "parameters": {"type": "object", "properties": {}},
            },
            {
                "name": "move_mouse",
                "description": (
                    "Move the pointer to a visible location. Coordinates are normalized "
                    "to the current shared frame: left/top=0 and right/bottom=1000."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "x": {"type": "number", "description": "0 to 1000."},
                        "y": {"type": "number", "description": "0 to 1000."},
                        "duration_seconds": {"type": "number"},
                    },
                    "required": ["x", "y"],
                },
            },
            {
                "name": "click_mouse",
                "description": (
                    "Click a visible location using normalized 0-1000 coordinates. "
                    "Use clicks=2 for a double-click."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "x": {"type": "number", "description": "0 to 1000."},
                        "y": {"type": "number", "description": "0 to 1000."},
                        "button": {
                            "type": "string",
                            "enum": ["left", "right", "middle"],
                        },
                        "clicks": {"type": "integer"},
                        "interval_seconds": {"type": "number"},
                    },
                    "required": ["x", "y"],
                },
            },
            {
                "name": "locate_target_on_screen",
                "description": (
                    "Find the precise normalized (0-1000) coordinates of a "
                    "described on-screen element using a dedicated pointing "
                    "model. Use this, or one of the *_on_description / "
                    "*_between_descriptions tools, instead of guessing x/y "
                    "yourself when precision matters."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "description": {
                            "type": "string",
                            "description": (
                                "A specific description of the target, e.g. "
                                "'the blue Submit button' or 'the Chrome icon "
                                "in the taskbar'."
                            ),
                        }
                    },
                    "required": ["description"],
                },
            },
            {
                "name": "click_on_description",
                "description": (
                    "Preferred way to click something. Describe the target in "
                    "words (not coordinates); a dedicated pointing model finds "
                    "its exact location and this clicks it. Use this instead of "
                    "click_mouse whenever you can describe the target, since "
                    "guessing raw x/y coordinates yourself is unreliable."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "description": {
                            "type": "string",
                            "description": "What to click, in plain words.",
                        },
                        "button": {
                            "type": "string",
                            "enum": ["left", "right", "middle"],
                        },
                        "clicks": {"type": "integer"},
                        "interval_seconds": {"type": "number"},
                    },
                    "required": ["description"],
                },
            },
            {
                "name": "move_to_description",
                "description": (
                    "Preferred way to move the pointer to something. Describe "
                    "the target in words; a dedicated pointing model finds its "
                    "exact location and the pointer moves there."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "description": {"type": "string"},
                        "duration_seconds": {"type": "number"},
                    },
                    "required": ["description"],
                },
            },
            {
                "name": "drag_between_descriptions",
                "description": (
                    "Preferred way to drag from one described target to "
                    "another, e.g. dragging a file onto a folder."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "start_description": {"type": "string"},
                        "end_description": {"type": "string"},
                        "duration_seconds": {"type": "number"},
                        "button": {
                            "type": "string",
                            "enum": ["left", "right", "middle"],
                        },
                    },
                    "required": ["start_description", "end_description"],
                },
            },
            {
                "name": "drag_mouse",
                "description": (
                    "Drag from one visible location to another using normalized "
                    "0-1000 coordinates."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "start_x": {"type": "number"},
                        "start_y": {"type": "number"},
                        "end_x": {"type": "number"},
                        "end_y": {"type": "number"},
                        "duration_seconds": {"type": "number"},
                        "button": {
                            "type": "string",
                            "enum": ["left", "right", "middle"],
                        },
                    },
                    "required": ["start_x", "start_y", "end_x", "end_y"],
                },
            },
            {
                "name": "scroll_mouse",
                "description": (
                    "Scroll vertically or horizontally. Positive vertical values "
                    "scroll up and negative values scroll down. Optional x/y are "
                    "normalized 0-1000 coordinates for the scroll target."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "vertical_clicks": {"type": "integer"},
                        "horizontal_clicks": {"type": "integer"},
                        "x": {"type": "number"},
                        "y": {"type": "number"},
                    },
                },
            },
            {
                "name": "type_text",
                "description": (
                    "Type or paste text into the currently focused control. The "
                    "clipboard method supports Unicode and is preferred."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "interval_seconds": {"type": "number"},
                        "press_enter": {"type": "boolean"},
                        "use_clipboard": {"type": "boolean"},
                    },
                    "required": ["text"],
                },
            },
            {
                "name": "press_key",
                "description": (
                    "Press a keyboard key one or more times, such as enter, tab, esc, "
                    "up, down, left, right, backspace, delete, or f5."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string"},
                        "presses": {"type": "integer"},
                        "interval_seconds": {"type": "number"},
                    },
                    "required": ["key"],
                },
            },
            {
                "name": "press_hotkey",
                "description": (
                    "Press a keyboard shortcut. Example: ['ctrl', 's'], "
                    "['alt', 'tab'], or ['win', 'r']."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "keys": {
                            "type": "array",
                            "items": {"type": "string"},
                        }
                    },
                    "required": ["keys"],
                },
            },
            {
                "name": "wait_for_screen",
                "description": (
                    "Wait briefly for an application, menu, page, or animation to "
                    "update before inspecting the next screen frame."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {"seconds": {"type": "number"}},
                },
            },
            {
                "name": "set_screen_capture",
                "description": (
                    "Enable or disable continuous screen vision and optionally select "
                    "a monitor. Index 1 is normally primary; index 0 is all monitors."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "enabled": {"type": "boolean"},
                        "monitor_index": {"type": "integer"},
                    },
                    "required": ["enabled"],
                },
            },
            {
                "name": "get_screen_capture_status",
                "description": (
                    "Return screen-sharing status, selected monitor, available "
                    "monitors, and latest-frame information."
                ),
                "parameters": {"type": "object", "properties": {}},
            },
            {
                "name": "remember_memory",
                "description": (
                    "Save or update a durable memory when the user explicitly asks "
                    "to remember or retain information across sessions."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "memory_key": {"type": "string"},
                        "memory_value": {"type": "string"},
                        "category": {"type": "string"},
                    },
                    "required": ["memory_key", "memory_value"],
                },
            },
            {
                "name": "search_memory",
                "description": "Search durable memory.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "list_memories",
                "description": "List recently updated durable memories.",
                "parameters": {
                    "type": "object",
                    "properties": {"limit": {"type": "integer"}},
                },
            },
            {
                "name": "forget_memory",
                "description": "Delete memory when the user explicitly asks.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "memory_id": {"type": "integer"},
                        "query": {"type": "string"},
                    },
                },
            },
            {
                "name": "recall_recent_conversation",
                "description": "Retrieve recent transcribed conversation turns.",
                "parameters": {
                    "type": "object",
                    "properties": {"limit": {"type": "integer"}},
                },
            },
            {
                "name": "recent_terminal_history",
                "description": "Retrieve recent CMD and PowerShell command results.",
                "parameters": {
                    "type": "object",
                    "properties": {"limit": {"type": "integer"}},
                },
            },
        ]
    }
]


async def dispatch_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    try:
        if name == "run_cmd_command":
            return await run_cmd_command(
                command=arguments.get("command", ""),
                working_directory=arguments.get("working_directory", ""),
                timeout_seconds=arguments.get(
                    "timeout_seconds", DEFAULT_COMMAND_TIMEOUT_SECONDS
                ),
            )

        if name == "run_powershell_command":
            return await run_powershell_command(
                command=arguments.get("command", ""),
                working_directory=arguments.get("working_directory", ""),
                timeout_seconds=arguments.get(
                    "timeout_seconds", DEFAULT_COMMAND_TIMEOUT_SECONDS
                ),
            )

        gui_functions = {
            "get_computer_control_status": get_computer_control_status,
            "locate_target_on_screen": lambda: locate_target_on_screen(
                arguments.get("description", "")
            ),
            "click_on_description": lambda: click_on_description(
                arguments.get("description", ""),
                arguments.get("button", "left"),
                arguments.get("clicks", 1),
                arguments.get("interval_seconds", 0.12),
            ),
            "move_to_description": lambda: move_to_description(
                arguments.get("description", ""),
                arguments.get("duration_seconds", 0.2),
            ),
            "drag_between_descriptions": lambda: drag_between_descriptions(
                arguments.get("start_description", ""),
                arguments.get("end_description", ""),
                arguments.get("duration_seconds", 0.7),
                arguments.get("button", "left"),
            ),
            "move_mouse": lambda: move_mouse(
                arguments.get("x"),
                arguments.get("y"),
                arguments.get("duration_seconds", 0.2),
            ),
            "click_mouse": lambda: click_mouse(
                arguments.get("x"),
                arguments.get("y"),
                arguments.get("button", "left"),
                arguments.get("clicks", 1),
                arguments.get("interval_seconds", 0.12),
            ),
            "drag_mouse": lambda: drag_mouse(
                arguments.get("start_x"),
                arguments.get("start_y"),
                arguments.get("end_x"),
                arguments.get("end_y"),
                arguments.get("duration_seconds", 0.7),
                arguments.get("button", "left"),
            ),
            "scroll_mouse": lambda: scroll_mouse(
                arguments.get("vertical_clicks", 0),
                arguments.get("horizontal_clicks", 0),
                arguments.get("x"),
                arguments.get("y"),
            ),
            "type_text": lambda: type_text(
                arguments.get("text", ""),
                arguments.get("interval_seconds", 0.01),
                arguments.get("press_enter", False),
                arguments.get("use_clipboard", True),
            ),
            "press_key": lambda: press_key(
                arguments.get("key", ""),
                arguments.get("presses", 1),
                arguments.get("interval_seconds", 0.08),
            ),
            "press_hotkey": lambda: press_hotkey(arguments.get("keys", [])),
            "wait_for_screen": lambda: wait_for_screen(
                arguments.get("seconds", 1.0)
            ),
        }
        if name in gui_functions:
            return await asyncio.to_thread(gui_functions[name])

        if name == "set_screen_capture":
            monitor_index = arguments.get("monitor_index")
            return set_screen_capture(
                enabled=arguments.get("enabled", True),
                monitor_index=(
                    int(monitor_index) if monitor_index is not None else None
                ),
            )

        if name == "get_screen_capture_status":
            return get_screen_capture_status()

        if name == "remember_memory":
            return remember_memory(
                memory_key=arguments.get("memory_key", ""),
                memory_value=arguments.get("memory_value", ""),
                category=arguments.get("category", "general"),
            )

        if name == "search_memory":
            return search_memory(
                query=arguments.get("query", ""),
                limit=arguments.get("limit", 10),
            )

        if name == "list_memories":
            return list_memories(arguments.get("limit", 20))

        if name == "forget_memory":
            memory_id = arguments.get("memory_id")
            return forget_memory(
                memory_id=int(memory_id) if memory_id is not None else None,
                query=arguments.get("query", ""),
            )

        if name == "recall_recent_conversation":
            return recall_recent_conversation(arguments.get("limit", 10))

        if name == "recent_terminal_history":
            return recent_terminal_history(arguments.get("limit", 10))

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


# =========================================================
# TRANSCRIPTION HELPERS
# =========================================================


def merge_transcription(existing: str, incoming: str) -> str:
    """Merge incremental or chunked transcription messages."""
    existing = str(existing or "").strip()
    incoming = str(incoming or "").strip()

    if not incoming:
        return existing
    if not existing:
        return incoming
    if incoming == existing:
        return existing
    if incoming.startswith(existing):
        return incoming
    if existing.endswith(incoming):
        return existing
    return f"{existing} {incoming}".strip()


async def clear_queue(queue: asyncio.Queue[bytes]) -> None:
    while True:
        try:
            queue.get_nowait()
            queue.task_done()
        except asyncio.QueueEmpty:
            return


# =========================================================
# LIVE VOICE ASSISTANT
# =========================================================


async def main() -> None:
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Set GEMINI_API_KEY or GOOGLE_API_KEY before running the program."
        )

    if os.name != "nt":
        raise RuntimeError("This program is intended for Windows.")

    enable_windows_dpi_awareness()
    configure_pyautogui()
    initialize_memory_database()
    memory_context = build_memory_context()

    system_instruction = f"""
You are Kira, A Female persistent Windows voice assistant with direct screen vision, mouse,
keyboard, separate CMD and PowerShell tools, and a local SQLite memory system.

COMPUTER CONTROL BEHAVIOR
- When the user asks you to click, select, open, close, drag, type, press a
  key, or use a shortcut, use the dedicated tool for it.
- You (the voice model) are not reliable at judging exact pixel coordinates.
  For clicking, moving to, or dragging a visible on-screen element, PREFER
  click_on_description, move_to_description, and drag_between_descriptions:
  describe the target in plain words (e.g. "the blue Submit button", "the
  Chrome icon in the taskbar") and a separate dedicated pointing model will
  find its exact location for you. Only use click_mouse/move_mouse/drag_mouse
  with raw x/y numbers if you already have coordinates from
  locate_target_on_screen, or for trivial cases like scrolling at a rough
  spot on screen.
- Mouse tools that do take raw coordinates use normalized values relative to
  the latest selected screen frame: left=0, right=1000, top=0, bottom=1000.
  x increases left-to-right, y increases top-to-bottom. Always pass x first,
  then y.
- Every frame has a faint labeled reference grid drawn on it, with numbers
  along the top edge (x values) and left edge (y values) at every 100 units,
  to help you describe locations. When in doubt, still prefer the
  description-based tools over eyeballing this grid yourself.
- Avoid the exact screen corners because the PyAutoGUI emergency fail-safe
  can abort the action there.
- If an action misses (the next screen frame shows no change or the wrong
  element highlighted), do not repeat the same call blindly. Re-describe the
  target more specifically and try again, or re-check the new frame first.
- For uncertain interfaces, perform one small action, inspect the next screen
  frame, and then continue. Use wait_for_screen when a page or application needs
  time to update.
- Do not claim an action succeeded merely because a tool returned. Verify the
  visible result when practical.
- Prefer direct GUI tools for visible UI interaction. Use terminal tools for
  command-line, scripting, system-management, installation, or file operations.
- Do not interact with the computer unless the user asks you to perform an action.

TERMINAL BEHAVIOR
- run_cmd_command and run_powershell_command are separate tools. Never pass CMD
  syntax to PowerShell or PowerShell syntax to CMD unless intentionally nested.
- Use PowerShell for structured Windows management and CMD when CMD behavior is
  more appropriate.
- Choose the correct working directory and timeout.
- Report exit_code, stdout, and stderr accurately. Never falsely claim success.

INTERRUPTION BEHAVIOR
- The user may speak while you are speaking. Stop the current spoken response
  immediately and listen to the newest instruction.

SCREEN VISION BEHAVIOR
- You receive a refreshed image of the selected Windows screen at up to one frame
  per second. A red marker may indicate the current pointer position.
- Use the latest frame when the user says "look at my screen", "what is this",
  "help me here", or asks you to operate something currently displayed.
- Describe only what is actually visible. If text or a target is unclear, state
  that and avoid guessing a dangerous location.
- Use set_screen_capture only when the user asks to enable, disable, pause, resume,
  or switch the shared monitor.

MEMORY BEHAVIOR
- Use remember_memory when the user explicitly asks you to remember information.
- Use search_memory when the user references earlier information, asks "what
  did I tell you about X", or you are about to state a fact about the user
  that is not already visible in this conversation. Call the tool first,
  then answer from its actual result.
- Never state a remembered fact unless it came from the CURRENT PERSISTENT
  CONTEXT below, a successful search_memory/list_memories call, or
  recall_recent_conversation in this session. Do not invent, assume, or
  extrapolate memory content, names, dates, or preferences that were not
  actually returned by a tool or shown in this conversation.
- If search_memory returns no results, say plainly that you don't have that
  saved instead of guessing an answer.
- Use forget_memory when the user explicitly asks you to forget something.
- Never claim that memory was saved unless the memory tool succeeded.
- If two memories conflict, prefer the one with the later updated_at and say
  so if it matters, rather than silently picking one.

CURRENT PERSISTENT CONTEXT
{memory_context}
""".strip()

    config = {
        "response_modalities": ["AUDIO"],
        "input_audio_transcription": {},
        "output_audio_transcription": {},
        "speech_config": {
            "voice_config": {
                "prebuilt_voice_config": {"voice_name": VOICE_NAME}
            }
        },
        "realtime_input_config": {
            # Explicitly enable barge-in: detected user speech interrupts
            # the model's current spoken response.
            "activity_handling": "START_OF_ACTIVITY_INTERRUPTS",
            "automatic_activity_detection": {
                "disabled": False,
                "prefix_padding_ms": 100,
                "silence_duration_ms": 700,
            },
        },
        "tools": TOOLS,
        "system_instruction": system_instruction,
    }

    client = genai.Client(api_key=api_key)
    audio = pyaudio.PyAudio()

    mic = None
    speaker = None
    tasks: list[asyncio.Task[Any]] = []

    send_lock = asyncio.Lock()
    speaker_lock = asyncio.Lock()

    # The microphone is paused only while a local tool is executing. It stays
    # active while Gemini speaks so the user can interrupt by speaking.
    pause_mic_for_tool = asyncio.Event()
    playback_queue: asyncio.Queue[bytes] = asyncio.Queue()

    user_transcript = ""
    assistant_transcript = ""

    try:
        mic = audio.open(
            format=FORMAT,
            channels=CHANNELS,
            rate=RATE_IN,
            input=True,
            frames_per_buffer=CHUNK,
        )

        speaker = audio.open(
            format=FORMAT,
            channels=CHANNELS,
            rate=RATE_OUT,
            output=True,
            frames_per_buffer=CHUNK,
        )

        print("Connecting to Gemini Live API...")

        async with client.aio.live.connect(model=MODEL, config=config) as session:
            print("Connected. Speak normally. Press Ctrl+C to stop.")
            print("VOICE INTERRUPTION ENABLED: speak while Gemini is talking.")
            print("SEPARATE CMD AND POWERSHELL TOOLS ARE ENABLED.")
            print("MOUSE AND KEYBOARD CONTROL IS ENABLED.")
            print(
                "EMERGENCY STOP: move the pointer to a screen corner or press Ctrl+C."
            )
            print(
                "SCREEN VISION: "
                f"{'ENABLED' if SCREEN_STATE['enabled'] else 'DISABLED'} "
                f"on monitor index {SCREEN_STATE['monitor_index']} at "
                f"{SCREEN_FPS:.1f} FPS."
            )
            print("Say 'stop sharing my screen' to disable screen vision.")
            print(f"Default working directory: {DEFAULT_WORKING_DIRECTORY}")
            print(f"Persistent memory database: {MEMORY_DB}\n")

            async def send_audio() -> None:
                stream_end_sent = False

                while True:
                    data = await asyncio.to_thread(
                        mic.read,
                        CHUNK,
                        exception_on_overflow=False,
                    )

                    if pause_mic_for_tool.is_set():
                        if not stream_end_sent:
                            async with send_lock:
                                if pause_mic_for_tool.is_set():
                                    await session.send_realtime_input(
                                        audio_stream_end=True
                                    )
                                    stream_end_sent = True
                        continue

                    stream_end_sent = False
                    async with send_lock:
                        if pause_mic_for_tool.is_set():
                            continue
                        await session.send_realtime_input(
                            audio=types.Blob(
                                data=data,
                                mime_type=f"audio/pcm;rate={RATE_IN}",
                            )
                        )

            async def send_screen_frames() -> None:
                # The Live API permits at most one video frame per second.
                frame_interval = max(1.0, 1.0 / max(SCREEN_FPS, 0.01))
                last_error_log_at = 0.0

                while True:
                    started_at = time.monotonic()

                    if not SCREEN_STATE["enabled"]:
                        await asyncio.sleep(0.25)
                        continue

                    try:
                        frame, metadata = await asyncio.to_thread(
                            capture_screen_frame
                        )

                        async with send_lock:
                            if SCREEN_STATE["enabled"]:
                                await session.send_realtime_input(
                                    video=types.Blob(
                                        data=frame,
                                        mime_type="image/jpeg",
                                    )
                                )

                        SCREEN_STATE["last_frame_at"] = utc_now()
                        SCREEN_STATE["last_frame_width"] = metadata["sent_width"]
                        SCREEN_STATE["last_frame_height"] = metadata["sent_height"]
                        SCREEN_STATE["last_frame_bytes"] = metadata["jpeg_bytes"]
                        SCREEN_STATE["last_error"] = None

                    except asyncio.CancelledError:
                        raise
                    except Exception as error:
                        error_text = f"{type(error).__name__}: {error}"
                        SCREEN_STATE["last_error"] = error_text

                        now = time.monotonic()
                        if (
                            now - last_error_log_at
                            >= SCREEN_ERROR_LOG_INTERVAL_SECONDS
                        ):
                            print(f"\n[Screen capture error] {error_text}")
                            last_error_log_at = now

                        await asyncio.sleep(SCREEN_CAPTURE_RETRY_SECONDS)
                        continue

                    elapsed = time.monotonic() - started_at
                    await asyncio.sleep(max(0.0, frame_interval - elapsed))

            async def play_audio() -> None:
                while True:
                    audio_data = await playback_queue.get()
                    try:
                        # Synchronize writes with speaker flushing during
                        # interruption. Each item is only about 40 ms long.
                        async with speaker_lock:
                            await asyncio.to_thread(speaker.write, audio_data)
                    except Exception as error:
                        # A write failure used to kill this task silently,
                        # after which every future response would still
                        # transcribe but never be heard. Log it and try to
                        # recover the stream instead of letting the task die.
                        print(
                            "\n[Speaker write error] "
                            f"{type(error).__name__}: {error}"
                        )
                        try:
                            if not speaker.is_active():
                                speaker.start_stream()
                        except Exception:
                            pass
                    finally:
                        playback_queue.task_done()

            def reset_speaker_stream() -> None:
                """Recover the output stream only if it actually stopped.

                Earlier versions unconditionally called stop_stream() then
                start_stream() here on every interruption. On Windows this
                can leave the underlying PortAudio/WASAPI stream in a state
                where later write() calls silently no-op, which is why voice
                output would work once and then go silent for the rest of
                the session even though the mic kept transcribing fine.
                Dropping the not-yet-written audio (clear_queue, below) is
                sufficient to stop playback promptly; we only touch the
                hardware stream if it has actually gone inactive.
                """
                if not speaker.is_active():
                    speaker.start_stream()

            async def interrupt_playback() -> None:
                # Remove audio not yet written; do not reset the hardware
                # stream unless it is no longer active (see reset_speaker_stream).
                await clear_queue(playback_queue)
                async with speaker_lock:
                    await asyncio.to_thread(reset_speaker_stream)

            async def handle_tool_calls(message: Any) -> None:
                pause_mic_for_tool.set()
                function_responses: list[types.FunctionResponse] = []

                try:
                    for call in message.tool_call.function_calls:
                        call_id = str(call.id)
                        name = str(call.name)
                        arguments = dict(call.args or {})

                        print(f"\n[Tool requested] {name}")
                        print(json.dumps(arguments, indent=2, ensure_ascii=False))

                        if call_id in processed_tool_calls:
                            result = processed_tool_calls[call_id]
                            print("[Duplicate tool-call ID: cached result reused]")
                        else:
                            result = await dispatch_tool(name, arguments)
                            processed_tool_calls[call_id] = result

                            if (
                                len(processed_tool_calls)
                                > PROCESSED_TOOL_CALL_CACHE_SIZE
                            ):
                                oldest_id = next(iter(processed_tool_calls))
                                processed_tool_calls.pop(oldest_id, None)

                        print("[Tool result]")
                        print(json.dumps(result, indent=2, ensure_ascii=False))

                        function_responses.append(
                            types.FunctionResponse(
                                id=call.id,
                                name=call.name,
                                response={"result": result},
                            )
                        )

                    async with send_lock:
                        await session.send_tool_response(
                            function_responses=function_responses
                        )
                finally:
                    # Resume microphone streaming immediately after the tool
                    # result is returned. It remains active during playback.
                    pause_mic_for_tool.clear()

            async def receive_audio_tools_and_transcripts() -> None:
                nonlocal user_transcript, assistant_transcript

                # session.receive() finishes after a model turn, so re-enter it.
                while True:
                    async for message in session.receive():
                        if message.tool_call:
                            await handle_tool_calls(message)

                        content = message.server_content
                        if not content:
                            continue

                        if content.input_transcription:
                            incoming = content.input_transcription.text or ""
                            user_transcript = merge_transcription(
                                user_transcript,
                                incoming,
                            )
                            if incoming.strip():
                                print(f"\rUser: {user_transcript}", end="", flush=True)

                        if content.output_transcription:
                            incoming = content.output_transcription.text or ""
                            assistant_transcript = merge_transcription(
                                assistant_transcript,
                                incoming,
                            )

                        if content.interrupted:
                            await interrupt_playback()
                            print("\n[Response interrupted — listening]")

                        if content.model_turn:
                            for part in content.model_turn.parts:
                                if part.inline_data and part.inline_data.data:
                                    audio_bytes = part.inline_data.data
                                    # Split large server chunks into short
                                    # playback pieces for responsive barge-in.
                                    for offset in range(
                                        0,
                                        len(audio_bytes),
                                        PLAYBACK_CHUNK_BYTES,
                                    ):
                                        await playback_queue.put(
                                            audio_bytes[
                                                offset : offset
                                                + PLAYBACK_CHUNK_BYTES
                                            ]
                                        )

                        if content.turn_complete:
                            await playback_queue.join()

                            completed_user_text = user_transcript.strip()
                            completed_assistant_text = assistant_transcript.strip()
                            save_conversation(
                                completed_user_text,
                                completed_assistant_text,
                            )

                            if completed_user_text:
                                print(f"\nUser transcript: {completed_user_text}")
                            if completed_assistant_text:
                                print(
                                    "Assistant transcript: "
                                    f"{completed_assistant_text}"
                                )

                            user_transcript = ""
                            assistant_transcript = ""
                            print("\nListening...")

            tasks = [
                asyncio.create_task(send_audio(), name="microphone-sender"),
                asyncio.create_task(
                    send_screen_frames(),
                    name="screen-frame-sender",
                ),
                asyncio.create_task(play_audio(), name="speaker-player"),
                asyncio.create_task(
                    receive_audio_tools_and_transcripts(),
                    name="live-receiver",
                ),
            ]

            done, pending = await asyncio.wait(
                tasks,
                return_when=asyncio.FIRST_EXCEPTION,
            )

            for task in done:
                error = task.exception()
                if error is not None:
                    raise error

            await asyncio.gather(*pending)

    finally:
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        if mic is not None:
            try:
                if mic.is_active():
                    mic.stop_stream()
            finally:
                mic.close()

        if speaker is not None:
            try:
                if speaker.is_active():
                    speaker.stop_stream()
            finally:
                speaker.close()

        audio.terminate()
        client.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nSession ended by user.")
    except Exception as error:
        print(f"\nFatal error: {type(error).__name__}: {error}")