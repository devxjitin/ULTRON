# Ultron

Ultron is a voice-controlled desktop assistant for Windows, built on Google's
Gemini Live API. You talk to it naturally, and it talks back — while seeing
your screen or webcam, controlling your mouse and keyboard, running terminal
commands, and remembering things across sessions.

It's designed for one thing: give a voice assistant real hands on your
computer, with sane guardrails, so you can get things done by just asking.

> **Warning**
> Ultron can execute shell commands and directly control your mouse and
> keyboard with the same permissions as the process running it. Only run it
> in an environment you trust — a disposable account, a sandbox, or a VM —
> and never as Administrator. An emergency stop is built in: slam the mouse
> into any screen corner, or press `Ctrl+C` in the console, to halt
> in-progress automation immediately.

## What it can do

**Conversation**
- Continuous, natural two-way voice conversation, including barge-in — you
  can interrupt Ultron mid-sentence and it stops and listens.
- Persistent long-term memory: it can remember facts you tell it and recall
  them in later sessions, and it keeps a searchable transcript of past
  conversations.

**Vision**
- Live streaming of either your screen or your webcam, so it can see and
  reason about what's in front of it while you talk.
- Voice-controlled switching between screen and camera, and between
  monitors on a multi-monitor setup.

**Computer control**
- Mouse: move, click, double-click, drag, scroll, press-and-hold.
- Keyboard: type text, press individual keys, and fire keyboard shortcuts.
- Point-and-click by description — tell it what to click ("the Save button",
  "the Chrome icon") rather than exact coordinates, and it locates and
  clicks it for you.
- Window management: list open windows, focus one, or launch an application
  by name.
- Clipboard read access.

**Terminal**
- Dedicated CMD and PowerShell execution tools, with output capture and
  configurable timeouts, so it can run real commands and report back exactly
  what happened.

**Autonomy, on request**
- A continuous-task mode: ask it to keep doing something on its own (for
  example, "keep an eye on this and let me know when it changes") and it
  will check in and act periodically until the task is done or you tell it
  to stop. It never starts this on its own — only when you explicitly ask.

## Requirements

- Windows 10 or 11
- Python 3.11+
- A Gemini API key ([Google AI Studio](https://aistudio.google.com/))
- A working microphone and speakers/headphones
- A webcam, if you want camera vision (optional — screen sharing works
  without one)

## Setup

1. Clone the repository and install dependencies:

   ```powershell
   pip install -r requirements.txt
   ```

2. Set your API key for the current session:

   ```powershell
   set GEMINI_API_KEY=your_api_key_here
   ```

3. Run it:

   ```powershell
   python main.py
   ```

On first run, Windows will prompt for microphone and (if enabled) camera
permission — allow both. Ultron starts listening immediately; just speak
naturally.

## Usage notes

- Speak naturally — there's no wake word or push-to-talk; the assistant is
  always listening while running and responds to normal conversational
  speech.
- Ask it to switch between screen sharing and camera, or to select a
  different monitor, at any time — no restart needed.
- Ask it to remember something ("remember that my Wi-Fi password is on the
  fridge") and it will recall that fact in future sessions.
- Stop the program at any time with `Ctrl+C` in the console it's running in.

## Safety

- Automation has built-in limits: capped typed-text length, capped repeat
  key presses, capped click counts, and capped command timeouts, so a
  runaway instruction can't run forever.
- The mouse-to-corner emergency stop works during any in-progress automated
  action.
- Terminal commands and their output are logged to local memory so you can
  review what was actually run.

## License

No license has been declared for this project yet — all rights reserved by
default until one is added.
