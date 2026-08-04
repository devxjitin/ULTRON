"""Windows CMD and PowerShell command execution helpers."""

from __future__ import annotations

import asyncio
import locale
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from config import (
    DEFAULT_COMMAND_TIMEOUT_SECONDS,
    DEFAULT_WORKING_DIRECTORY,
    MAX_COMMAND_TIMEOUT_SECONDS,
    MAX_TOOL_OUTPUT_CHARACTERS,
)
from memory.db import save_terminal_history


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
