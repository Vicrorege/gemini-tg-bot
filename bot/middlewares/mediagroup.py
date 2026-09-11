"""Middleware to collect and batch media group (album) updates into a single event."""

import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict, List
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message

logger = logging.getLogger(__name__)


class MediaGroupMiddleware(BaseMiddleware):
    def __init__(self, latency: float = 0.5):
        self.latency = latency
        self.media_groups: Dict[str, List[Message]] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        if not isinstance(event, Message) or not event.media_group_id:
            return await handler(event, data)

        mg_id = str(event.media_group_id)

        if mg_id not in self.media_groups:
            self.media_groups[mg_id] = [event]

            # Debounce loop: wait until all messages for this media_group_id arrive
            max_wait_iterations = 10
            iterations = 0
            while iterations < max_wait_iterations:
                prev_len = len(self.media_groups[mg_id])
                await asyncio.sleep(self.latency)
                if len(self.media_groups[mg_id]) == prev_len:
                    break
                iterations += 1

            album = self.media_groups.pop(mg_id, [])
            if not album:
                return

            data["album"] = album
            return await handler(event, data)
        else:
            self.media_groups[mg_id].append(event)
            return
