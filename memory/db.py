"""SQLite-backed durable memory, conversation transcripts, and terminal history."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from config import (
    MAX_RECENT_MEMORIES_IN_PROMPT,
    MAX_RECENT_TURNS_IN_PROMPT,
    MEMORY_DB,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def get_db() -> Iterator[sqlite3.Connection]:
    """Open a connection for one unit of work and always close it.

    sqlite3.Connection's own context-manager protocol only commits/rolls
    back the transaction on exit — it does not close the connection. Every
    call site here used `with get_db() as db:`, so each one was silently
    leaking a connection (and an OS file handle) for the life of the
    process. Wrapping it in a real contextmanager keeps the same commit/
    rollback behavior while guaranteeing the connection is closed.
    """
    connection = sqlite3.connect(MEMORY_DB, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        with connection:
            yield connection
    finally:
        connection.close()


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
