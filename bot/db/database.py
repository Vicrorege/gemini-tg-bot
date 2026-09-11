"""Async SQLite database manager."""

import aiosqlite
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator, List, Optional, Tuple

from bot.config import config
from bot.db.models import Message, Session, UserSetting


class Database:
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or str(config.get_db_path())

    @asynccontextmanager
    async def get_connection(self) -> AsyncGenerator[aiosqlite.Connection, None]:
        conn = await aiosqlite.connect(self.db_path)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA foreign_keys = ON")
        await conn.execute("PRAGMA journal_mode = WAL")
        try:
            yield conn
        finally:
            await conn.close()

    async def init_db(self):
        """Initialize database schema tables and indexes."""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        async with self.get_connection() as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    title TEXT NOT NULL DEFAULT 'Новый диалог',
                    model TEXT,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    summary TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_sessions_user_active
                ON sessions(user_id, is_active);
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    is_compressed INTEGER NOT NULL DEFAULT 0,
                    tokens INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_messages_session
                ON messages(session_id, is_compressed);
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS user_settings (
                    user_id INTEGER PRIMARY KEY,
                    selected_model TEXT,
                    system_prompt TEXT,
                    web_search_mode TEXT NOT NULL DEFAULT 'auto',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

            # Migration: Ensure web_search_mode column exists in user_settings
            async with db.execute("PRAGMA table_info(user_settings)") as cursor:
                columns = [row["name"] for row in await cursor.fetchall()]
                if "web_search_mode" not in columns:
                    await db.execute("ALTER TABLE user_settings ADD COLUMN web_search_mode TEXT NOT NULL DEFAULT 'auto'")

            await db.commit()

    async def get_or_create_active_session(self, user_id: int) -> Session:
        """Fetch current active session for user, or create a new one."""
        async with self.get_connection() as db:
            async with db.execute(
                "SELECT * FROM sessions WHERE user_id = ? AND is_active = 1 ORDER BY updated_at DESC LIMIT 1",
                (user_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return self._row_to_session(row)

            # Deactivate any lingering sessions
            await db.execute("UPDATE sessions SET is_active = 0 WHERE user_id = ?", (user_id,))
            
            # Fetch user's preferred model if set
            async with db.execute("SELECT selected_model FROM user_settings WHERE user_id = ?", (user_id,)) as cur:
                user_row = await cur.fetchone()
                user_model = user_row["selected_model"] if user_row else config.default_model

            # Create new session
            cursor = await db.execute(
                "INSERT INTO sessions (user_id, title, model, is_active) VALUES (?, ?, ?, 1)",
                (user_id, "Новый диалог", user_model)
            )
            session_id = cursor.lastrowid
            await db.commit()

            async with db.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)) as cur:
                row = await cur.fetchone()
                return self._row_to_session(row)

    async def create_new_session(self, user_id: int, title: Optional[str] = None, model: Optional[str] = None) -> Session:
        """Deactivate active sessions and create a fresh new session."""
        async with self.get_connection() as db:
            await db.execute("UPDATE sessions SET is_active = 0 WHERE user_id = ?", (user_id,))

            if not model:
                async with db.execute("SELECT selected_model FROM user_settings WHERE user_id = ?", (user_id,)) as cur:
                    user_row = await cur.fetchone()
                    model = user_row["selected_model"] if user_row else config.default_model

            cursor = await db.execute(
                "INSERT INTO sessions (user_id, title, model, is_active) VALUES (?, ?, ?, 1)",
                (user_id, title or "Новый диалог", model)
            )
            session_id = cursor.lastrowid
            await db.commit()

            async with db.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)) as cur:
                row = await cur.fetchone()
                return self._row_to_session(row)

    async def get_session(self, session_id: int) -> Optional[Session]:
        async with self.get_connection() as db:
            async with db.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)) as cursor:
                row = await cursor.fetchone()
                return self._row_to_session(row) if row else None

    async def get_user_sessions(self, user_id: int, limit: int = 10, offset: int = 0) -> Tuple[List[Session], int]:
        """Returns (sessions_list, total_count)."""
        async with self.get_connection() as db:
            async with db.execute("SELECT COUNT(*) as cnt FROM sessions WHERE user_id = ?", (user_id,)) as cur:
                total = (await cur.fetchone())["cnt"]

            async with db.execute(
                "SELECT * FROM sessions WHERE user_id = ? ORDER BY is_active DESC, updated_at DESC LIMIT ? OFFSET ?",
                (user_id, limit, offset)
            ) as cursor:
                rows = await cursor.fetchall()
                return [self._row_to_session(r) for r in rows], total

    async def switch_active_session(self, user_id: int, session_id: int) -> bool:
        """Set session_id as the user's active session."""
        async with self.get_connection() as db:
            # Check ownership
            async with db.execute("SELECT id FROM sessions WHERE id = ? AND user_id = ?", (session_id, user_id)) as cur:
                if not await cur.fetchone():
                    return False

            await db.execute("UPDATE sessions SET is_active = 0 WHERE user_id = ?", (user_id,))
            await db.execute(
                "UPDATE sessions SET is_active = 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (session_id,)
            )
            await db.commit()
            return True

    async def update_session_title(self, session_id: int, title: str):
        async with self.get_connection() as db:
            await db.execute(
                "UPDATE sessions SET title = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (title, session_id)
            )
            await db.commit()

    async def update_session_summary(self, session_id: int, summary: str):
        async with self.get_connection() as db:
            await db.execute(
                "UPDATE sessions SET summary = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (summary, session_id)
            )
            await db.commit()

    async def delete_session(self, user_id: int, session_id: int) -> bool:
        async with self.get_connection() as db:
            async with db.execute("SELECT is_active FROM sessions WHERE id = ? AND user_id = ?", (session_id, user_id)) as cur:
                row = await cur.fetchone()
                if not row:
                    return False
                was_active = bool(row["is_active"])

            await db.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            await db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))

            if was_active:
                # Activate most recent session if exists
                await db.execute("""
                    UPDATE sessions SET is_active = 1 
                    WHERE id = (SELECT id FROM sessions WHERE user_id = ? ORDER BY updated_at DESC LIMIT 1)
                """, (user_id,))

            await db.commit()
            return True

    async def clear_session_messages(self, session_id: int):
        async with self.get_connection() as db:
            await db.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            await db.execute(
                "UPDATE sessions SET summary = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (session_id,)
            )
            await db.commit()

    async def add_message(self, session_id: int, role: str, content: str, tokens: int = 0) -> Message:
        async with self.get_connection() as db:
            cursor = await db.execute(
                "INSERT INTO messages (session_id, role, content, tokens) VALUES (?, ?, ?, ?)",
                (session_id, role, content, tokens)
            )
            msg_id = cursor.lastrowid
            await db.execute("UPDATE sessions SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (session_id,))
            await db.commit()

            async with db.execute("SELECT * FROM messages WHERE id = ?", (msg_id,)) as cur:
                row = await cur.fetchone()
                return self._row_to_message(row)

    async def get_messages(self, session_id: int, only_uncompressed: bool = True) -> List[Message]:
        async with self.get_connection() as db:
            query = "SELECT * FROM messages WHERE session_id = ?"
            params = [session_id]
            if only_uncompressed:
                query += " AND is_compressed = 0"
            query += " ORDER BY id ASC"

            async with db.execute(query, params) as cursor:
                rows = await cursor.fetchall()
                return [self._row_to_message(r) for r in rows]

    async def mark_messages_compressed(self, message_ids: List[int]):
        if not message_ids:
            return
        async with self.get_connection() as db:
            placeholders = ",".join("?" for _ in message_ids)
            await db.execute(
                f"UPDATE messages SET is_compressed = 1 WHERE id IN ({placeholders})",
                message_ids
            )
            await db.commit()

    async def get_user_settings(self, user_id: int) -> UserSetting:
        async with self.get_connection() as db:
            async with db.execute("SELECT * FROM user_settings WHERE user_id = ?", (user_id,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    return self._row_to_user_setting(row)

            # Insert default
            await db.execute(
                "INSERT INTO user_settings (user_id, selected_model, system_prompt, web_search_mode) VALUES (?, ?, ?, ?)",
                (user_id, config.default_model, config.system_prompt, config.default_web_mode)
            )
            await db.commit()
            async with db.execute("SELECT * FROM user_settings WHERE user_id = ?", (user_id,)) as cur:
                return self._row_to_user_setting(await cur.fetchone())

    async def set_user_model(self, user_id: int, model: str):
        async with self.get_connection() as db:
            await db.execute("""
                INSERT INTO user_settings (user_id, selected_model, updated_at) 
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id) DO UPDATE SET selected_model = excluded.selected_model, updated_at = CURRENT_TIMESTAMP
            """, (user_id, model))
            # Also update active session model
            await db.execute("UPDATE sessions SET model = ? WHERE user_id = ? AND is_active = 1", (model, user_id))
            await db.commit()

    async def set_user_system_prompt(self, user_id: int, prompt: str):
        async with self.get_connection() as db:
            await db.execute("""
                INSERT INTO user_settings (user_id, system_prompt, updated_at) 
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id) DO UPDATE SET system_prompt = excluded.system_prompt, updated_at = CURRENT_TIMESTAMP
            """, (user_id, prompt))
            await db.commit()

    async def set_user_web_search_mode(self, user_id: int, mode: str):
        valid_modes = {"auto", "on", "off"}
        mode = mode.lower() if mode.lower() in valid_modes else "auto"
        async with self.get_connection() as db:
            await db.execute("""
                INSERT INTO user_settings (user_id, web_search_mode, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id) DO UPDATE SET web_search_mode = excluded.web_search_mode, updated_at = CURRENT_TIMESTAMP
            """, (user_id, mode))
            await db.commit()

    @staticmethod
    def _row_to_session(row) -> Session:
        return Session(
            id=row["id"],
            user_id=row["user_id"],
            title=row["title"],
            model=row["model"],
            is_active=bool(row["is_active"]),
            summary=row["summary"],
            created_at=row["created_at"],
            updated_at=row["updated_at"]
        )

    @staticmethod
    def _row_to_message(row) -> Message:
        return Message(
            id=row["id"],
            session_id=row["session_id"],
            role=row["role"],
            content=row["content"],
            is_compressed=bool(row["is_compressed"]),
            tokens=row["tokens"],
            created_at=row["created_at"]
        )

    @staticmethod
    def _row_to_user_setting(row) -> UserSetting:
        keys = row.keys() if hasattr(row, "keys") else []
        web_mode = row["web_search_mode"] if "web_search_mode" in keys else config.default_web_mode
        return UserSetting(
            user_id=row["user_id"],
            selected_model=row["selected_model"],
            system_prompt=row["system_prompt"],
            web_search_mode=web_mode,
            created_at=row["created_at"],
            updated_at=row["updated_at"]
        )


db = Database()
