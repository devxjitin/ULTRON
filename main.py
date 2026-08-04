"""
Ultron: a real-time voice assistant for Windows with:
- Continuous two-way voice conversation with voice barge-in interruption
- Dedicated CMD and PowerShell tools
- Mouse movement, click, double-click, drag, and scrolling
- Keyboard typing, key presses, and hotkeys
- Persistent SQLite long-term memory
- Automatic conversation transcript storage
- Continuous Windows screen vision streamed live
- Voice-controlled screen-sharing enable/disable and monitor selection

Install dependencies:
    pip install -r requirements.txt

Set your API key before running:
    set ASSISTANT_API_KEY=your_api_key_here

WARNING:
This program gives the model direct access to your Windows shell, mouse, and
keyboard with the same permissions as the Python process. Run it only in a
disposable test account, Windows Sandbox, or VM. Do not run it as Administrator.
Move the mouse rapidly to a screen corner or press Ctrl+C in the console to stop.
"""

from __future__ import annotations

import asyncio

from voice.assistant import main

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nSession ended by user.")
    except Exception as error:
        print(f"\nFatal error: {type(error).__name__}: {error}")
