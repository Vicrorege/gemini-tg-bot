from aiogram import Router
from bot.handlers.commands import router as commands_router
from bot.handlers.sessions import router as sessions_router
from bot.handlers.inline import router as inline_router
from bot.handlers.multimodal import router as multimodal_router
from bot.handlers.chat import router as chat_router

main_router = Router()
main_router.include_router(commands_router)
main_router.include_router(sessions_router)
main_router.include_router(inline_router)
main_router.include_router(multimodal_router)
main_router.include_router(chat_router)

__all__ = ["main_router"]
