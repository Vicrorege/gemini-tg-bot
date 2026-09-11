"""Inline query handler for using the bot across any chat with web intelligence."""

import hashlib
import logging
from aiogram import Router
from aiogram.types import InlineQuery, InlineQueryResultArticle, InputTextMessageContent

from bot.config import config
from bot.llm.client import llm
from bot.services.grounding import grounding_service
from bot.services.search_router import search_router
from bot.services.web_search import web_search_service

logger = logging.getLogger(__name__)

router = Router(name="inline")


@router.inline_query()
async def handle_inline_query(query: InlineQuery):
    user_id = query.from_user.id

    # Check whitelist
    if config.allowed_telegram_ids and user_id not in config.allowed_telegram_ids:
        article = InlineQueryResultArticle(
            id="unauthorized",
            title="⛔️ Доступ ограничен",
            description="Ваш Telegram ID не находится в списке разрешенных.",
            input_message_content=InputTextMessageContent(
                message_text="⛔️ У вас нет доступа к использованию этого бота.",
                parse_mode="Markdown"
            )
        )
        await query.answer([article], cache_time=10, is_personal=True)
        return

    user_query = query.query.strip()
    if not user_query:
        article = InlineQueryResultArticle(
            id="help",
            title="💡 Задайте вопрос Gemini",
            description="Напишите текст вопроса или темы...",
            input_message_content=InputTextMessageContent(
                message_text="💬 Чтобы задать вопрос, напишите: `@bot <ваш вопрос>`",
                parse_mode="Markdown"
            )
        )
        await query.answer([article], cache_time=5, is_personal=True)
        return

    try:
        force_search = user_query.startswith("!s ") or user_query.startswith("/search ")
        clean_q = user_query
        if force_search:
            clean_q = user_query.split(" ", 1)[1].strip()

        # Check search need
        decision = await search_router.analyze(clean_q, force_search=force_search)
        grounded_context = ""
        if decision.need_search and decision.queries:
            results = await web_search_service.multi_search(decision.queries, max_per_query=3)
            if results:
                grounded_context, _ = grounding_service.build_grounded_context(results, [])

        effective_system = grounding_service.build_system_prompt(config.system_prompt, grounded_context)

        resp = await llm.client.chat.completions.create(
            model=config.default_model,
            messages=[
                {"role": "system", "content": effective_system},
                {"role": "user", "content": clean_q}
            ],
            temperature=0.7,
            max_tokens=1500,
        )
        answer_text = resp.choices[0].message.content if (resp.choices and resp.choices[0].message.content) else "*(Пустой ответ)*"

        msg_content = f"🤖 *Вопрос:* _{clean_q}_\n\n{answer_text}"
        query_hash = hashlib.md5(user_query.encode("utf-8")).hexdigest()

        article = InlineQueryResultArticle(
            id=query_hash,
            title="✨ Ответ от Gemini (с поиском в сети)" if grounded_context else "✨ Ответ от Gemini",
            description=answer_text[:120].replace("\n", " "),
            input_message_content=InputTextMessageContent(
                message_text=msg_content,
                parse_mode="Markdown"
            )
        )
        await query.answer([article], cache_time=10, is_personal=True)

    except Exception as e:
        logger.error(f"Error handling inline query '{user_query}': {e}")
        err_article = InlineQueryResultArticle(
            id="error",
            title="⚠️ Ошибка генерации",
            description=str(e),
            input_message_content=InputTextMessageContent(
                message_text=f"⚠️ *Ошибка при запросе к Gemini:* {str(e)}",
                parse_mode="Markdown"
            )
        )
        await query.answer([err_article], cache_time=5, is_personal=True)
