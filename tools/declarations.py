"""Gemini function-declaration schema for every tool Ultron can call."""

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
                "name": "list_open_windows",
                "description": (
                    "List currently open, visible window titles and whether "
                    "each is active/minimized/maximized. Use this to see what "
                    "applications are already running before deciding whether "
                    "to focus_window an existing one or launch_application a "
                    "new one."
                ),
                "parameters": {"type": "object", "properties": {}},
            },
            {
                "name": "focus_window",
                "description": (
                    "Bring an already-open window to the foreground by a "
                    "(partial, case-insensitive) title match, e.g. "
                    "'WhatsApp' or 'Notepad'. Preferred over clicking a "
                    "taskbar icon by guessed coordinates when switching "
                    "between apps that are already running."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {
                            "type": "string",
                            "description": (
                                "Full or partial window title to match, e.g. "
                                "'WhatsApp' or 'Visual Studio Code'."
                            ),
                        }
                    },
                    "required": ["title"],
                },
            },
            {
                "name": "launch_application",
                "description": (
                    "Launch an application, open a file/folder, or open a URL "
                    "— the same as double-clicking it or typing it into the "
                    "Windows Run dialog. Accepts an executable name on PATH "
                    "(e.g. 'notepad', 'calc', 'chrome'), a full path, or a "
                    "URL. Only use this for something not already open; "
                    "prefer focus_window for switching to something already "
                    "running."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target": {
                            "type": "string",
                            "description": (
                                "What to launch/open, e.g. 'notepad', "
                                "'C:\\\\path\\\\to\\\\file.docx', or "
                                "'https://example.com'."
                            ),
                        }
                    },
                    "required": ["target"],
                },
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
                "name": "mouse_down",
                "description": (
                    "Press and hold a mouse button at a location without "
                    "releasing it. Use only for things click_mouse/drag_mouse "
                    "can't do in one step, like a press-and-hold interaction "
                    "or a multi-point drag. You MUST call mouse_up afterward "
                    "or the button stays held down."
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
                    },
                    "required": ["x", "y"],
                },
            },
            {
                "name": "mouse_up",
                "description": "Release a mouse button previously held with mouse_down.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "button": {
                            "type": "string",
                            "enum": ["left", "right", "middle"],
                        }
                    },
                },
            },
            {
                "name": "scroll_mouse",
                "description": (
                    "Scroll vertically or horizontally in real wheel notches "
                    "(1 notch = one physical mouse-wheel click). Positive "
                    "vertical values scroll up and negative values scroll "
                    "down. A typical scroll to read more content is 3-6; use "
                    "larger values (10-20+) to move a long distance quickly. "
                    "Optional x/y are normalized 0-1000 coordinates for the "
                    "scroll target."
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
                "name": "get_clipboard_text",
                "description": (
                    "Read the current clipboard text without changing it. Use "
                    "this to check what was just copied, e.g. after a copy "
                    "hotkey, or when the user references something they copied."
                ),
                "parameters": {"type": "object", "properties": {}},
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
                "name": "key_down",
                "description": (
                    "Press and hold a key without releasing it. Use only for "
                    "things press_key/press_hotkey can't do, like holding "
                    "Shift while making several separate clicks to "
                    "multi-select, or holding an arrow key for continuous "
                    "movement. You MUST call key_up with the same key "
                    "afterward or it stays held down."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {"key": {"type": "string"}},
                    "required": ["key"],
                },
            },
            {
                "name": "key_up",
                "description": "Release a key previously held with key_down.",
                "parameters": {
                    "type": "object",
                    "properties": {"key": {"type": "string"}},
                    "required": ["key"],
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
                "name": "set_camera_capture",
                "description": (
                    "Enable or disable webcam vision and optionally select which "
                    "camera device to use. There is no visible preview window; "
                    "frames are only sent to you. Screen and camera vision can "
                    "both be on at once -- when they are, the camera appears as "
                    "a small picture-in-picture thumbnail on the screen frame. "
                    "Only call this when the user explicitly asks to turn the "
                    "camera on/off or switch camera devices."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "enabled": {"type": "boolean"},
                        "camera_index": {"type": "integer"},
                    },
                    "required": ["enabled"],
                },
            },
            {
                "name": "get_camera_capture_status",
                "description": (
                    "Return webcam status, selected camera device, available "
                    "camera devices, and latest-frame information."
                ),
                "parameters": {"type": "object", "properties": {}},
            },
            {
                "name": "start_continuous_task",
                "description": (
                    "Start continuous task mode for an ongoing task the user "
                    "just asked for, e.g. 'keep replying to them on WhatsApp, "
                    "whatever they say' or 'keep monitoring this download'. "
                    "Once started, you receive a periodic nudge to check on "
                    "and continue the task on your own, without the user "
                    "needing to re-prompt you each time. Only call this for "
                    "an explicit, ongoing instruction — never on your own "
                    "initiative."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "description": {
                            "type": "string",
                            "description": (
                                "A short summary of the ongoing task and how "
                                "to keep handling it, e.g. 'Reply naturally "
                                "to whatever the person on the other end of "
                                "the open WhatsApp chat says next.'"
                            ),
                        }
                    },
                    "required": ["description"],
                },
            },
            {
                "name": "stop_continuous_task",
                "description": (
                    "Stop continuous task mode. Call this yourself once the "
                    "task is clearly finished or there is nothing left to do, "
                    "or whenever the user explicitly asks you to stop."
                ),
                "parameters": {"type": "object", "properties": {}},
            },
            {
                "name": "get_continuous_task_status",
                "description": (
                    "Return whether continuous task mode is active and, if so, "
                    "its current task description."
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
