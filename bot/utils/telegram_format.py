"""Utilities for formatting text, message chunking, and mention handling."""

import re
from typing import List, Optional
from aiogram.types import Message

MAX_TG_MESSAGE_LENGTH = 4000  # Conservative limit below 4096


def split_message_text(text: str, max_length: int = MAX_TG_MESSAGE_LENGTH) -> List[str]:
    """
    Split text into chunks smaller than max_length, trying to preserve
    code blocks and paragraphs cleanly.
    """
    if len(text) <= max_length:
        return [text]

    chunks: List[str] = []
    remaining = text

    while len(remaining) > max_length:
        # Look for optimal split points: double newline (paragraph), single newline, code block boundary, space
        split_idx = -1

        sub = remaining[:max_length]
        p_idx = sub.rfind("\n\n")
        if p_idx > max_length // 2:
            split_idx = p_idx + 2
        else:
            nl_idx = sub.rfind("\n")
            if nl_idx > max_length // 2:
                split_idx = nl_idx + 1
            else:
                sp_idx = sub.rfind(" ")
                if sp_idx > max_length // 2:
                    split_idx = sp_idx + 1
                else:
                    split_idx = max_length

        chunk = remaining[:split_idx]
        remaining = remaining[split_idx:]

        # Handle unclosed code block tags across splits
        code_block_count = chunk.count("```")
        if code_block_count % 2 != 0:
            chunk += "\n```"
            remaining = "```\n" + remaining

        chunks.append(chunk)

    if remaining:
        chunks.append(remaining)

    return chunks


def is_message_for_bot(message: Message, bot_username: Optional[str], bot_id: Optional[int]) -> bool:
    """Check if message is intended for the bot (in private chat or via mention/reply in groups)."""
    if message.chat.type == "private":
        return True

    text = (message.text or message.caption or "").lower()
    if bot_username and f"@{bot_username.lower()}" in text:
        return True

    if message.reply_to_message and message.reply_to_message.from_user:
        if bot_id and message.reply_to_message.from_user.id == bot_id:
            return True

    return False


def clean_bot_mention(text: str, bot_username: Optional[str]) -> str:
    """Strip @bot_username from the prompt text."""
    if not text:
        return ""
    if not bot_username:
        return text.strip()
    cleaned = re.sub(rf"@{bot_username}\b", "", text, flags=re.IGNORECASE).strip()
    return cleaned


def escape_markdown_v2(text: str) -> str:
    """Helper to escape MarkdownV2 special characters if needed."""
    escape_chars = r"_*[]()~`>#+-=|{}.!"
    return re.sub(f"([{re.escape(escape_chars)}])", r"\\\1", text)
