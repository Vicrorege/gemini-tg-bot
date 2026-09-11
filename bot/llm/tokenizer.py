"""Tokenizer utilities for estimating token counts in messages."""

from typing import Any, Dict, List, Union
import tiktoken

try:
    _encoder = tiktoken.get_encoding("cl100k_base")
except Exception:
    _encoder = None


def estimate_tokens(text: Union[str, List[Dict[str, Any]]]) -> int:
    """Estimate token count for a text or multimodal content."""
    if not text:
        return 0

    if isinstance(text, str):
        if _encoder:
            try:
                return len(_encoder.encode(text))
            except Exception:
                pass
        # Fallback estimation: ~4 chars per token for English, ~2 chars per token for Cyrillic/multilingual
        return max(1, int(len(text) / 3))

    if isinstance(text, list):
        total = 0
        for item in text:
            if isinstance(item, dict):
                t = item.get("type")
                if t == "text":
                    total += estimate_tokens(item.get("text", ""))
                elif t == "image_url":
                    # Standard high-res vision token estimate (around 765 tokens)
                    total += 765
        return total

    return 10


def count_messages_tokens(messages: List[Dict[str, Any]]) -> int:
    """Estimate total tokens across a list of message dicts."""
    total = 0
    for msg in messages:
        # Per-message overhead (role, framing)
        total += 4
        content = msg.get("content", "")
        total += estimate_tokens(content)
    total += 2  # priming
    return total
