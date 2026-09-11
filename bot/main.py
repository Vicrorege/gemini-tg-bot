"""Entrypoint for Gemini Telegram Bot."""

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from bot.config import config
from bot.db.database import db
from bot.handlers import main_router
from bot.middlewares.auth import AuthMiddleware
from bot.middlewares.mediagroup import MediaGroupMiddleware
from bot.middlewares.session import SessionMiddleware

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

logger = logging.getLogger("gemini-bot")


async def set_bot_commands(bot: Bot):
    """Register command menu in Telegram UI."""
    commands = [
        BotCommand(command="search", description="🔍 Найти ответ в интернете"),
        BotCommand(command="web", description="🌐 Режим веб-поиска (авто/вкл/выкл)"),
        BotCommand(command="new", description="✨ Начать новую сессию диалога"),
        BotCommand(command="sessions", description="🗂 Список всех диалогов и переключение"),
        BotCommand(command="rename", description="✏️ Переименовать текущую сессию"),
        BotCommand(command="clear", description="🧹 Очистить историю текущей сессии"),
        BotCommand(command="delete", description="🗑 Удалить текущую сессию"),
        BotCommand(command="model", description="🤖 Выбрать или посмотреть модель"),
        BotCommand(command="compress", description="🧠 Принудительно сжать контекст"),
        BotCommand(command="stats", description="📊 Статистика токенов и сессии"),
        BotCommand(command="help", description="📖 Подробная справка"),
    ]
    try:
        await bot.set_my_commands(commands)
        logger.info("Bot commands successfully registered.")
    except Exception as e:
        logger.warning(f"Could not register bot commands: {e}")


async def main():
    if not config.bot_token:
        logger.error("BOT_TOKEN is not set! Please set BOT_TOKEN in .env or environment variables.")
        sys.exit(1)

    logger.info("Starting Gemini Telegram Bot...")
    logger.info(f"OpenAI Base URL: {config.openai_base_url}")
    logger.info(f"Default Model: {config.default_model}")
    logger.info(f"Telegram Proxy: {config.telegram_proxy_url or 'None (Direct connection)'}")
    logger.info(f"Allowed Telegram IDs: {config.allowed_telegram_ids or 'ALL (No restriction)'}")

    # Initialize Database
    await db.init_db()
    logger.info("Database initialized successfully.")

    bot_session = None
    if config.telegram_proxy_url:
        bot_session = AiohttpSession(proxy=config.telegram_proxy_url)

    bot = Bot(
        token=config.bot_token,
        session=bot_session,
        default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN),
    )
    dp = Dispatcher()

    # Register Middlewares
    dp.message.middleware(AuthMiddleware())
    dp.callback_query.middleware(AuthMiddleware())
    dp.inline_query.middleware(AuthMiddleware())
    dp.message.middleware(MediaGroupMiddleware())
    dp.message.middleware(SessionMiddleware())
    dp.callback_query.middleware(SessionMiddleware())

    # Include Handlers
    dp.include_router(main_router)

    # Setup menu
    await set_bot_commands(bot)

    # Delete any pending webhook/updates before polling
    await bot.delete_webhook(drop_pending_updates=True)

    try:
        await dp.start_polling(bot, allowed_updates=["message", "callback_query", "inline_query"])
    finally:
        await bot.session.close()
        logger.info("Bot shut down gracefully.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped.")
