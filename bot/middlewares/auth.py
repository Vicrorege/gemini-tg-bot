"""Authentication and Whitelist middleware."""

import logging
from typing import Any, Awaitable, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery

from bot.config import config

logger = logging.getLogger(__name__)


class AuthMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        allowed_ids = config.allowed_telegram_ids
        if not allowed_ids:
            # Whitelist is empty: allow all
            return await handler(event, data)

        user_id = None
        is_private = True

        if isinstance(event, Message):
            if event.from_user:
                user_id = event.from_user.id
            is_private = event.chat.type == "private"
        elif isinstance(event, CallbackQuery):
            if event.from_user:
                user_id = event.from_user.id
            if event.message:
                is_private = event.message.chat.type == "private"
        elif isinstance(event, InlineQuery):
            if event.from_user:
                user_id = event.from_user.id

        if user_id is None:
            return await handler(event, data)

        if user_id not in allowed_ids:
            logger.warning(f"Unauthorized access attempt from Telegram ID {user_id}")
            if is_private:
                if isinstance(event, Message):
                    await event.answer(
                        "⛔️ *Доступ ограничен.*\n\n"
                        "Ваш Telegram ID не находится в списке разрешенных пользователей.\n"
                        f"Ваш ID: `{user_id}`",
                        parse_mode="Markdown"
                    )
                elif isinstance(event, CallbackQuery):
                    await event.answer("⛔️ У вас нет доступа к этому боту.", show_alert=True)
            return

        return await handler(event, data)
