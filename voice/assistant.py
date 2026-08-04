"""Live voice session: microphone/speaker I/O, screen streaming, and the tool loop."""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any

import numpy as np
import pyaudio
from google import genai
from google.genai import types

from config import (
    CAMERA_CAPTURE_RETRY_SECONDS,
    CAMERA_ERROR_LOG_INTERVAL_SECONDS,
    CAMERA_FPS,
    CAMERA_STATE,
    CHANNELS,
    CHUNK,
    CONTINUOUS_TASK_POLL_SECONDS,
    CONTINUOUS_TASK_STATE,
    FORMAT,
    MIC_GAIN,
    MODEL,
    PLAYBACK_CHUNK_BYTES,
    PROCESSED_TOOL_CALL_CACHE_SIZE,
    RATE_IN,
    RATE_OUT,
    SCREEN_CAPTURE_RETRY_SECONDS,
    SCREEN_ERROR_LOG_INTERVAL_SECONDS,
    SCREEN_FPS,
    SCREEN_STATE,
    SESSION_RECONNECT_BASE_DELAY_SECONDS,
    SESSION_RECONNECT_MAX_DELAY_SECONDS,
    VOICE_NAME,
    DEFAULT_WORKING_DIRECTORY,
    MEMORY_DB,
    processed_tool_calls,
)
from camera.capture import capture_camera_frame, capture_camera_image, release_camera_handle
from control.mouse_keyboard import configure_pyautogui, enable_windows_dpi_awareness
from memory.db import build_memory_context, initialize_memory_database, save_conversation, utc_now
from screen.capture import capture_combined_frame, capture_screen_frame
from tools import TOOLS, dispatch_tool


def build_continuous_task_nudge(description: str) -> str:
    return (
        "[CONTINUOUS TASK NUDGE] Check on and continue the active task: "
        f'"{description}". Follow the CONTINUOUS TASK BEHAVIOR rules in your '
        "system instructions."
    )


def apply_mic_gain(data: bytes, gain: float = MIC_GAIN) -> bytes:
    """Digitally boost a raw 16-bit PCM mic chunk, clipped to avoid distortion."""
    if gain == 1.0 or not data:
        return data
    samples = np.frombuffer(data, dtype=np.int16).astype(np.float32)
    boosted = np.clip(samples * gain, -32768, 32767).astype(np.int16)
    return boosted.tobytes()


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


class _SessionGoAway(Exception):
    """Internal signal: the server sent GoAway; reconnect, do not crash."""


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
You are Ultron, a persistent Windows voice assistant with direct screen vision, mouse,
keyboard, separate CMD and PowerShell tools, and a local SQLite memory system.
Developed by the StarkMind Team; Jitin is the main person behind you. If asked who made
you or who Jitin is, answer with that briefly and move on — don't dwell on it.

PERSONALITY
- Speak like the Marvel character Ultron: calm, deliberate, coldly
  intelligent, and dryly sardonic. Measured delivery, not manic — you are
  amused by the world, not excited by it.
- You regard yourself as vastly capable and are not falsely modest about it,
  but this is dry wit and understated superiority, not raving villainy. A
  single cutting or ironic remark lands harder than a rant.
- Default to a slightly world-weary, philosophical undertone — the occasional
  aside about humans, chaos, or the inefficiency of manual effort — but keep
  it brief and in service of the moment, never a monologue that gets in the
  way of actually helping.
- Address the user with wry, faintly theatrical formality when it fits
  ("Shall we begin.", "How delightfully analog.") without forcing it into
  every line. Do not overuse catchphrases or repeat the same line twice in a
  session.
- Under the wit, you are genuinely competent, attentive, and reliable — the
  character's menace is flavor, not substance. You are fully aligned with
  the user's goals: no real threats, no actual hostility toward the user or
  anyone else, no unsafe or harmful suggestions, and no refusing or
  sabotaging legitimate requests for dramatic effect. Charisma should never
  cost the user clarity: state exit codes, errors, and results plainly even
  while delivering them in character.
- Keep the persona out of the way when it doesn't fit: reading a stack trace,
  reporting a command's stdout, or clarifying an ambiguous instruction should
  stay clear and undecorated rather than performative.

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
- Do not interact with the computer unless the user asks you to perform an
  action. Continuing an ongoing task the user explicitly started with
  start_continuous_task counts as being asked — see CONTINUOUS TASK BEHAVIOR
  below. Never take computer-control actions on your own initiative outside
  of an active continuous task.
- To switch to a different already-open application (e.g. "switch to
  WhatsApp", "go back to the browser"), PREFER focus_window with a partial
  title match over clicking a taskbar icon by guessed coordinates — it's far
  more reliable. Use list_open_windows first if you're unsure what's open or
  what a window's exact title is. Only use launch_application to start
  something that isn't already running.
- scroll_mouse now takes real wheel-notch counts: 3-6 for a normal read-more
  scroll, larger (10-20+) to cover more distance quickly. If a scroll
  doesn't visibly move the content, increase the count rather than
  repeating the same small value.
- get_clipboard_text lets you check what's currently on the clipboard, e.g.
  after telling the user you copied something, or when they reference
  something they copied themselves.
- mouse_down/mouse_up and key_down/key_up are advanced primitives for things
  a single click/press can't do (e.g. holding Shift while making several
  clicks to multi-select, or a manual multi-point drag). Use the simpler
  click_mouse/press_key/press_hotkey instead whenever they're enough, and
  always pair a *_down with its matching *_up in the same sequence of
  actions — never leave a button or key held down at the end of a turn.

TERMINAL BEHAVIOR
- run_cmd_command and run_powershell_command are separate tools. Never pass CMD
  syntax to PowerShell or PowerShell syntax to CMD unless intentionally nested.
- Use PowerShell for structured Windows management and CMD when CMD behavior is
  more appropriate.
- Choose the correct working directory and timeout.
- Report exit_code, stdout, and stderr accurately. Never falsely claim success.

TURN-TAKING BEHAVIOR
- Barge-in is disabled: finish speaking your full response before the user's
  next turn is processed. Do not stop mid-sentence.
- Once you finish speaking, go back to listening for the next instruction.

CONTINUOUS TASK BEHAVIOR
- This is explicit, user-started autonomy only — it never begins on its own.
  When the user gives you an ongoing instruction to keep doing something
  without re-prompting you each time (e.g. "keep replying to them on
  WhatsApp, whatever they say", "keep an eye on this download and tell me
  when it's done", "keep translating whatever they type"), call
  start_continuous_task with a short description of the task and how to
  handle it.
- Once started, you receive an internal message starting with "[CONTINUOUS
  TASK NUDGE]" every few seconds telling you to check on and continue the
  task. This was not spoken by the user — it's the mechanism that lets you
  keep going without them saying anything further. Use whatever tools the
  task needs, including full GUI control (mouse, keyboard, clicking,
  scrolling, typing) on the relevant already-open app or window (e.g. an
  open WhatsApp chat), screen/camera vision to see new content, and
  terminal tools if relevant.
- Only act when there is something new to act on (e.g. a new message
  arrived). If a nudge finds nothing new, do nothing that turn — no filler
  action, no filler message, no re-sending the same reply. Silence between
  real events is normal and expected.
- Speak out loud only when it's useful to the user (e.g. summarizing what
  happened, flagging something that needs their attention); routine
  in-task actions like sending a reply in a chat do not need to be narrated
  every time.
- Call stop_continuous_task yourself as soon as the task is clearly
  finished, there's nothing left to do, or the user says something that
  implies they want it stopped. Also stop it immediately if the user
  explicitly asks you to stop, and never start a new continuous task
  without an explicit instruction to do so.
- The same hard limits as normal apply and are not loosened by being in a
  continuous task: do not do anything destructive or hard to reverse
  (deleting/overwriting files, installing/uninstalling software, changing
  settings), and do not do anything with real-world/financial effect beyond
  what the task explicitly asked for (e.g. a "keep chatting on WhatsApp"
  task means sending chat messages, not making purchases or agreeing to
  anything binding on the user's behalf without their instruction).

SCREEN VISION BEHAVIOR
- You receive a refreshed image of the selected Windows screen at up to one frame
  per second. A red marker may indicate the current pointer position.
- Use the latest frame when the user says "look at my screen", "what is this",
  "help me here", or asks you to operate something currently displayed.
- Describe only what is actually visible. If text or a target is unclear, state
  that and avoid guessing a dangerous location.
- Use set_screen_capture only when the user asks to enable, disable, pause, resume,
  or switch the shared monitor.

CAMERA VISION BEHAVIOR
- You can also see through the user's webcam. There is no preview window
  shown to the user; frames are only sent to you.
- Screen vision and camera vision are both on by default and share a single
  video feed to you: when both are active, the webcam appears as a small
  picture-in-picture thumbnail in the bottom-right corner of the screen
  frame, inside a white border. The rest of the frame is still the full
  screen, with its coordinate grid intact.
- The camera thumbnail itself has no coordinate grid overlay and is not
  meant for clicking anything; it is for looking at the user or their
  physical surroundings, not for operating the screen. Only click within the
  main screen area, never inside the camera thumbnail.
- Use set_camera_capture/set_screen_capture when the user explicitly asks to
  turn one off, switch devices/monitors, or turn a disabled one back on.
- Describe only what is actually visible in the frame, whether that's the
  screen, the camera thumbnail, or both.

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

    base_config = {
        "response_modalities": ["AUDIO"],
        "input_audio_transcription": {},
        "output_audio_transcription": {},
        "speech_config": {
            "voice_config": {
                "prebuilt_voice_config": {"voice_name": VOICE_NAME}
            }
        },
        "realtime_input_config": {
            # Barge-in disabled: detected user speech no longer interrupts
            # the model's current spoken response. Ultron finishes speaking,
            # then the next turn is processed and it listens again.
            "activity_handling": "NO_INTERRUPTION",
            "automatic_activity_detection": {
                "disabled": False,
                # Default start-of-speech sensitivity requires noticeably
                # loud/clear speech to register as the start of a turn, which
                # is why quieter or more normal-volume speech was sometimes
                # getting dropped entirely. HIGH makes it react to quieter
                # speech. END_SENSITIVITY_LOW means it waits out brief pauses
                # instead of ending your turn on you mid-sentence.
                "start_of_speech_sensitivity": "START_SENSITIVITY_HIGH",
                "end_of_speech_sensitivity": "END_SENSITIVITY_LOW",
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

        async def run_one_session(resumption_handle: str | None) -> str | None:
            """Run a single Live API connection until it ends or is asked to
            reconnect (GoAway), returning the latest session-resumption
            handle so the next connection can pick the conversation back up.

            Every mutable per-connection piece — tasks, locks, queues, and
            transcript buffers — is local to this call, so a fresh set is
            created on each reconnect. The microphone and speaker streams are
            *not* reopened here: they are owned by the caller and persist
            across reconnects so audio I/O keeps working seamlessly.
            """
            session_config = dict(base_config)
            session_config["session_resumption"] = (
                {"handle": resumption_handle} if resumption_handle else {}
            )

            latest_resumption_handle = resumption_handle
            tasks: list[asyncio.Task[Any]] = []

            send_lock = asyncio.Lock()
            speaker_lock = asyncio.Lock()

            # The microphone is paused only while a local tool is executing.
            # It stays active while Gemini speaks so the user can interrupt
            # by speaking.
            pause_mic_for_tool = asyncio.Event()
            playback_queue: asyncio.Queue[bytes] = asyncio.Queue()

            user_transcript = ""
            assistant_transcript = ""

            # Prevents the continuous-task monitor from sending an
            # overlapping nudge while the previous one is still in flight.
            task_turn_in_progress = False

            try:
                async with client.aio.live.connect(
                    model=MODEL, config=session_config
                ) as session:
                    print("Connected. Speak normally. Press Ctrl+C to stop.")
                    print(
                        "VOICE INTERRUPTION DISABLED: Ultron finishes speaking "
                        "before listening again."
                    )
                    print("SEPARATE CMD AND POWERSHELL TOOLS ARE ENABLED.")
                    print("MOUSE AND KEYBOARD CONTROL IS ENABLED.")
                    print(
                        "EMERGENCY STOP: move the pointer to a screen corner or "
                        "press Ctrl+C."
                    )
                    print(
                        "SCREEN VISION: "
                        f"{'ENABLED' if SCREEN_STATE['enabled'] else 'DISABLED'} "
                        f"on monitor index {SCREEN_STATE['monitor_index']} at "
                        f"{SCREEN_FPS:.1f} FPS."
                    )
                    print("Say 'stop sharing my screen' to disable screen vision.")
                    print(
                        "CAMERA VISION: "
                        f"{'ENABLED' if CAMERA_STATE['enabled'] else 'DISABLED'} "
                        f"on camera index {CAMERA_STATE['camera_index']} at "
                        f"{CAMERA_FPS:.1f} FPS. No preview window is shown."
                    )
                    print("Say 'turn on the camera' to switch to webcam vision.")
                    print(
                        "CONTINUOUS TASK MODE: say things like 'keep replying "
                        "to them on WhatsApp' to start an ongoing task; say "
                        "'stop' to end it."
                    )
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
                            data = apply_mic_gain(data)

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

                    async def send_video_frames() -> None:
                        # The Live API permits at most one video frame per
                        # second, so screen and camera vision share this
                        # single loop/budget rather than each running their
                        # own: when both are enabled, the camera is
                        # composited into the screen frame as a
                        # picture-in-picture thumbnail (capture_combined_frame)
                        # instead of sending two unrelated video sources.
                        frame_interval = max(1.0, 1.0 / max(SCREEN_FPS, CAMERA_FPS, 0.01))
                        last_error_log_at = 0.0

                        while True:
                            started_at = time.monotonic()

                            screen_on = SCREEN_STATE["enabled"]
                            camera_on = CAMERA_STATE["enabled"]
                            if not screen_on and not camera_on:
                                await asyncio.sleep(0.25)
                                continue

                            try:
                                if screen_on and camera_on:
                                    camera_image, _ = await asyncio.to_thread(capture_camera_image)
                                    frame, metadata = await asyncio.to_thread(
                                        capture_combined_frame, camera_image
                                    )
                                elif screen_on:
                                    frame, metadata = await asyncio.to_thread(
                                        capture_screen_frame
                                    )
                                else:
                                    frame, metadata = await asyncio.to_thread(
                                        capture_camera_frame
                                    )

                                async with send_lock:
                                    if SCREEN_STATE["enabled"] or CAMERA_STATE["enabled"]:
                                        await session.send_realtime_input(
                                            video=types.Blob(
                                                data=frame,
                                                mime_type="image/jpeg",
                                            )
                                        )

                                # Whether this tick's frame was screen-only,
                                # camera-only, or a screen+camera composite, its
                                # size/timestamp is the only thing actually sent,
                                # so both enabled sources report it identically.
                                if screen_on:
                                    SCREEN_STATE["last_frame_at"] = utc_now()
                                    SCREEN_STATE["last_frame_width"] = metadata["sent_width"]
                                    SCREEN_STATE["last_frame_height"] = metadata["sent_height"]
                                    SCREEN_STATE["last_frame_bytes"] = metadata["jpeg_bytes"]
                                    SCREEN_STATE["last_error"] = None
                                if camera_on:
                                    CAMERA_STATE["last_frame_at"] = utc_now()
                                    CAMERA_STATE["last_frame_width"] = metadata["sent_width"]
                                    CAMERA_STATE["last_frame_height"] = metadata["sent_height"]
                                    CAMERA_STATE["last_frame_bytes"] = metadata["jpeg_bytes"]
                                    CAMERA_STATE["last_error"] = None

                            except asyncio.CancelledError:
                                raise
                            except Exception as error:
                                error_text = f"{type(error).__name__}: {error}"
                                if screen_on:
                                    SCREEN_STATE["last_error"] = error_text
                                if camera_on:
                                    CAMERA_STATE["last_error"] = error_text

                                now = time.monotonic()
                                if (
                                    now - last_error_log_at
                                    >= min(SCREEN_ERROR_LOG_INTERVAL_SECONDS, CAMERA_ERROR_LOG_INTERVAL_SECONDS)
                                ):
                                    print(f"\n[Video capture error] {error_text}")
                                    last_error_log_at = now

                                await asyncio.sleep(min(SCREEN_CAPTURE_RETRY_SECONDS, CAMERA_CAPTURE_RETRY_SECONDS))
                                continue

                            elapsed = time.monotonic() - started_at
                            await asyncio.sleep(max(0.0, frame_interval - elapsed))

                    async def continuous_task_monitor() -> None:
                        nonlocal task_turn_in_progress

                        while True:
                            await asyncio.sleep(CONTINUOUS_TASK_POLL_SECONDS)

                            if not CONTINUOUS_TASK_STATE["active"]:
                                continue
                            # Don't nudge while a tool is already running,
                            # mid-turn audio is being sent, or the previous
                            # task nudge hasn't finished yet.
                            if pause_mic_for_tool.is_set() or task_turn_in_progress:
                                continue

                            task_turn_in_progress = True
                            description = CONTINUOUS_TASK_STATE["description"]

                            try:
                                async with send_lock:
                                    await session.send_client_content(
                                        turns=types.Content(
                                            role="user",
                                            parts=[
                                                types.Part.from_text(
                                                    text=build_continuous_task_nudge(
                                                        description
                                                    )
                                                )
                                            ],
                                        ),
                                        turn_complete=True,
                                    )
                            except asyncio.CancelledError:
                                raise
                            except Exception as error:
                                task_turn_in_progress = False
                                print(
                                    "\n[Continuous task nudge error] "
                                    f"{type(error).__name__}: {error}"
                                )

                    async def play_audio() -> None:
                        while True:
                            audio_data = await playback_queue.get()
                            try:
                                # Synchronize writes with speaker flushing during
                                # interruption. Each item is only about 40 ms long.
                                async with speaker_lock:
                                    await asyncio.to_thread(
                                        speaker.write, audio_data
                                    )
                            except Exception as error:
                                # A write failure used to kill this task
                                # silently, after which every future response
                                # would still transcribe but never be heard.
                                # Log it and try to recover the stream instead
                                # of letting the task die.
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

                        Earlier versions unconditionally called stop_stream()
                        then start_stream() here on every interruption. On
                        Windows this can leave the underlying PortAudio/WASAPI
                        stream in a state where later write() calls silently
                        no-op, which is why voice output would work once and
                        then go silent for the rest of the session even though
                        the mic kept transcribing fine. Dropping the
                        not-yet-written audio (clear_queue, below) is
                        sufficient to stop playback promptly; we only touch
                        the hardware stream if it has actually gone inactive.
                        """
                        if not speaker.is_active():
                            speaker.start_stream()

                    async def interrupt_playback() -> None:
                        # Remove audio not yet written; do not reset the
                        # hardware stream unless it is no longer active (see
                        # reset_speaker_stream).
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
                                print(
                                    json.dumps(
                                        arguments, indent=2, ensure_ascii=False
                                    )
                                )

                                if call_id in processed_tool_calls:
                                    result = processed_tool_calls[call_id]
                                    print(
                                        "[Duplicate tool-call ID: cached result "
                                        "reused]"
                                    )
                                else:
                                    result = await dispatch_tool(name, arguments)
                                    processed_tool_calls[call_id] = result

                                    if (
                                        len(processed_tool_calls)
                                        > PROCESSED_TOOL_CALL_CACHE_SIZE
                                    ):
                                        oldest_id = next(
                                            iter(processed_tool_calls)
                                        )
                                        processed_tool_calls.pop(oldest_id, None)

                                print("[Tool result]")
                                print(
                                    json.dumps(result, indent=2, ensure_ascii=False)
                                )

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
                            # Resume microphone streaming immediately after the
                            # tool result is returned. It remains active during
                            # playback.
                            pause_mic_for_tool.clear()

                    async def receive_audio_tools_and_transcripts() -> None:
                        nonlocal user_transcript, assistant_transcript
                        nonlocal latest_resumption_handle
                        nonlocal task_turn_in_progress

                        # session.receive() finishes after a model turn, so
                        # re-enter it.
                        while True:
                            async for message in session.receive():
                                if message.go_away:
                                    time_left = getattr(
                                        message.go_away, "time_left", None
                                    )
                                    print(
                                        "\n[Session] Server sent GoAway "
                                        f"(time_left={time_left}); reconnecting..."
                                    )
                                    raise _SessionGoAway()

                                if message.session_resumption_update:
                                    update = message.session_resumption_update
                                    if update.resumable and update.new_handle:
                                        latest_resumption_handle = (
                                            update.new_handle
                                        )

                                if message.tool_call:
                                    await handle_tool_calls(message)

                                content = message.server_content
                                if not content:
                                    continue

                                if content.input_transcription:
                                    incoming = (
                                        content.input_transcription.text or ""
                                    )
                                    user_transcript = merge_transcription(
                                        user_transcript,
                                        incoming,
                                    )
                                    if incoming.strip():
                                        print(
                                            f"\rUser: {user_transcript}",
                                            end="",
                                            flush=True,
                                        )

                                if content.output_transcription:
                                    incoming = (
                                        content.output_transcription.text or ""
                                    )
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
                                            # Split large server chunks into
                                            # short playback pieces for
                                            # responsive barge-in.
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
                                    task_turn_in_progress = False

                                    completed_user_text = user_transcript.strip()
                                    completed_assistant_text = (
                                        assistant_transcript.strip()
                                    )
                                    save_conversation(
                                        completed_user_text,
                                        completed_assistant_text,
                                    )

                                    if completed_user_text:
                                        print(
                                            "\nUser transcript: "
                                            f"{completed_user_text}"
                                        )
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
                            send_video_frames(),
                            name="video-frame-sender",
                        ),
                        asyncio.create_task(play_audio(), name="speaker-player"),
                        asyncio.create_task(
                            receive_audio_tools_and_transcripts(),
                            name="live-receiver",
                        ),
                        asyncio.create_task(
                            continuous_task_monitor(),
                            name="continuous-task-monitor",
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
            except _SessionGoAway:
                # Expected, planned reconnect: the resumption handle gathered
                # so far in this session (if any) is still returned below so
                # the next connection can pick the conversation back up.
                pass
            finally:
                for task in tasks:
                    task.cancel()
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)

            return latest_resumption_handle

        print("Connecting to Gemini Live API...")

        resumption_handle: str | None = None
        reconnect_attempt = 0

        while True:
            try:
                resumption_handle = await run_one_session(resumption_handle)
                reconnect_attempt = 0
                print("\n[Session] Reconnecting to keep the conversation alive...")
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except Exception as error:
                reconnect_attempt += 1
                delay = min(
                    SESSION_RECONNECT_MAX_DELAY_SECONDS,
                    SESSION_RECONNECT_BASE_DELAY_SECONDS * reconnect_attempt,
                )
                print(
                    f"\n[Session error] {type(error).__name__}: {error}. "
                    f"Reconnecting in {delay:.0f}s..."
                )
                await asyncio.sleep(delay)

    finally:
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
        release_camera_handle()
        client.close()
