"""Context compression and window management."""

import json
import logging
from typing import Any, Dict, List, Tuple

from bot.config import config
from bot.db.database import db
from bot.db.models import Message, Session
from bot.llm.client import llm
from bot.llm.tokenizer import count_messages_tokens, estimate_tokens

logger = logging.getLogger(__name__)


class ContextCompressor:
    @staticmethod
    def _parse_message_content(content_str: str) -> Any:
        """Parse content string (might be JSON array for multimodal or simple string)."""
        if content_str.startswith("[") or content_str.startswith("{"):
            try:
                return json.loads(content_str)
            except Exception:
                pass
        return content_str

    @classmethod
    async def prepare_context(
        cls,
        session: Session,
        system_prompt: str,
        new_user_message: Any = None,
    ) -> List[Dict[str, Any]]:
        """
        Build optimized context for the LLM.
        Applies automatic context compression if token budget is exceeded.
        """
        uncompressed_messages = await db.get_messages(session.id, only_uncompressed=True)

        # Build raw message dicts
        msg_dicts: List[Dict[str, Any]] = []
        for m in uncompressed_messages:
            msg_dicts.append({
                "id": m.id,
                "role": m.role,
                "content": cls._parse_message_content(m.content),
            })

        if new_user_message is not None:
            msg_dicts.append({
                "id": None,
                "role": "user",
                "content": new_user_message,
            })

        # Calculate tokens
        test_payload: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        if session.summary:
            test_payload.append({
                "role": "system",
                "content": f"[Архив/Краткое содержание предыдущего контекста]:\n{session.summary}"
            })
        test_payload.extend([{"role": m["role"], "content": m["content"]} for m in msg_dicts])

        total_tokens = count_messages_tokens(test_payload)
        logger.info(f"Session {session.id} current tokens: {total_tokens}/{config.max_context_tokens}")

        # Check if compression is needed
        protect_n = config.protect_last_n_messages
        # Messages from DB that can be compressed (excluding the new user message if present)
        db_messages = [m for m in msg_dicts if m["id"] is not None]

        if total_tokens > config.max_context_tokens and len(db_messages) > protect_n:
            logger.info(f"Triggering auto-compression for session {session.id} (total tokens {total_tokens})")
            await cls.compress_session(session, protect_last_n=protect_n)
            # Reload session and uncompressed messages after compression
            updated_session = await db.get_session(session.id)
            if updated_session:
                session = updated_session
            uncompressed_messages = await db.get_messages(session.id, only_uncompressed=True)
            msg_dicts = []
            for m in uncompressed_messages:
                msg_dicts.append({
                    "id": m.id,
                    "role": m.role,
                    "content": cls._parse_message_content(m.content),
                })
            if new_user_message is not None:
                msg_dicts.append({
                    "id": None,
                    "role": "user",
                    "content": new_user_message,
                })

        # Final message list assembly
        final_messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        if session.summary:
            final_messages.append({
                "role": "system",
                "content": f"[Архив/Краткое содержание предыдущего контекста]:\n{session.summary}"
            })

        for m in msg_dicts:
            final_messages.append({
                "role": m["role"],
                "content": m["content"]
            })

        return final_messages

    @classmethod
    async def compress_session(
        cls,
        session: Session,
        protect_last_n: int = 6,
    ) -> Tuple[bool, str]:
        """
        Compress older messages in a session into session.summary.
        Returns (success: bool, status_message: str).
        """
        uncompressed = await db.get_messages(session.id, only_uncompressed=True)
        if len(uncompressed) <= protect_last_n:
            return False, f"Недостаточно сообщений для сжатия (всего {len(uncompressed)}, защищено {protect_last_n})."

        to_compress = uncompressed[:-protect_last_n]
        msg_dicts = [{"role": m.role, "content": cls._parse_message_content(m.content)} for m in to_compress]

        new_summary = await llm.summarize_context(
            existing_summary=session.summary,
            messages_to_compress=msg_dicts,
            model=session.model
        )

        # Update DB
        await db.update_session_summary(session.id, new_summary)
        await db.mark_messages_compressed([m.id for m in to_compress])

        logger.info(f"Compressed {len(to_compress)} messages in session {session.id}.")
        return True, f"Успешно сжато {len(to_compress)} сообщений. Размер резюме: {len(new_summary)} симв."


compressor = ContextCompressor()
