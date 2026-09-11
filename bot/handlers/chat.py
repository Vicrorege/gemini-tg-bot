"""Core text chat handler with real-time streaming, mentions, web search, and replies."""

import asyncio
import logging
import time
from typing import Optional

from aiogram import Router, F
from aiogram.enums import ChatAction
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import Message

from bot.config import config
from bot.db.database import db
from bot.db.models import Session, UserSetting
from bot.llm.client import llm
from bot.llm.compressor import compressor
from bot.llm.tokenizer import estimate_tokens
from bot.services.grounding import grounding_service
from bot.services.search_router import search_router, SearchDecision
from bot.services.web_search import web_search_service
from bot.utils.telegram_format import clean_bot_mention, is_message_for_bot, split_message_text

logger = logging.getLogger(__name__)

router = Router(name="chat")


async def auto_title_task(session_id: int, user_text: str, model: Optional[str]):
    """Background task to generate a concise conversation title."""
    try:
        title = await llm.generate_title(user_text, model=model)
        await db.update_session_title(session_id, title)
        logger.info(f"Auto-generated title for session {session_id}: '{title}'")
    except Exception as e:
        logger.warning(f"Auto-titling failed for session {session_id}: {e}")


async def process_chat_generation(
    message: Message,
    session: Session,
    user_settings: UserSetting,
    prompt_text: str,
    force_search: bool = False,
    custom_search_query: Optional[str] = None,
):
    """Unified handler for processing chat messages, web retrieval, streaming, and DB saving."""
    bot_info = await message.bot.get_me()

    # Initial placeholder message
    status_msg = await message.reply("💬 *Обрабатываю запрос...*", parse_mode="Markdown")
    await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

    # 1. Web search & Web page retrieval decision
    search_results = []
    page_contents = []
    grounded_context = ""

    try:
        decision: SearchDecision = await search_router.analyze(
            text=custom_search_query or prompt_text,
            user_settings=user_settings,
            force_search=force_search,
        )

        # A. Fetch explicit URLs if present
        if decision.target_urls:
            try:
                first_url = decision.target_urls[0]
                await status_msg.edit_text(f"🌐 *Читаю страницу:* `{first_url[:50]}`...", parse_mode="Markdown")
            except Exception:
                pass

            for url in decision.target_urls[:2]:
                page_data = await web_search_service.fetch_url_content(url)
                if page_data:
                    page_contents.append(page_data)

        # B. Perform Web Search if needed
        if decision.need_search and decision.queries:
            query_display = decision.queries[0]
            try:
                await status_msg.edit_text(f"🔍 *Ищу в интернете:* _{query_display[:60]}_...", parse_mode="Markdown")
            except Exception:
                pass

            search_results = await web_search_service.multi_search(decision.queries, max_per_query=3)

        # C. Ground context if we gathered information
        if search_results or page_contents:
            grounded_context, _ = grounding_service.build_grounded_context(search_results, page_contents)
            try:
                await status_msg.edit_text("💬 *Генерирую ответ на основе данных из сети...*", parse_mode="Markdown")
            except Exception:
                pass

    except Exception as e:
        logger.warning(f"Error during search/fetch phase: {e}")

    # 2. System prompt and model setup
    base_system_prompt = user_settings.system_prompt or config.system_prompt
    effective_system_prompt = grounding_service.build_system_prompt(base_system_prompt, grounded_context)
    effective_model = session.model or user_settings.selected_model or config.default_model

    # 3. Context preparation with auto-compression
    messages = await compressor.prepare_context(
        session=session,
        system_prompt=effective_system_prompt,
        new_user_message=prompt_text,
    )

    # 4. Stream chat completion
    full_response = ""
    last_rendered_text = ""
    last_edit_time = time.monotonic()
    min_edit_interval = config.stream_update_interval

    try:
        async for chunk in llm.stream_chat(messages=messages, model=effective_model):
            full_response += chunk
            now = time.monotonic()

            if (now - last_edit_time) >= min_edit_interval and len(full_response) > len(last_rendered_text) + 15:
                preview = full_response[:3800] + (" ▌" if len(full_response) <= 3800 else "")
                try:
                    await status_msg.edit_text(preview, parse_mode="Markdown")
                    last_rendered_text = preview
                    last_edit_time = now
                except TelegramBadRequest as e:
                    if "message is not modified" not in str(e).lower():
                        try:
                            await status_msg.edit_text(preview, parse_mode=None)
                            last_rendered_text = preview
                            last_edit_time = now
                        except Exception:
                            pass
                except TelegramRetryAfter as e:
                    await asyncio.sleep(e.retry_after)
                except Exception as e:
                    logger.debug(f"Edit throttled or skipped: {e}")

    except Exception as e:
        logger.exception(f"Error during generation: {e}")
        full_response += f"\n\n⚠️ *Ошибка при генерации ответа: {str(e)}*"

    if not full_response.strip():
        full_response = "*(Модель вернула пустой ответ)*"

    # 5. Save to database
    user_tokens = estimate_tokens(prompt_text)
    assistant_tokens = estimate_tokens(full_response)
    await db.add_message(session.id, role="user", content=prompt_text, tokens=user_tokens)
    await db.add_message(session.id, role="assistant", content=full_response, tokens=assistant_tokens)

    # 6. Render final output
    chunks = split_message_text(full_response, max_length=4000)

    try:
        await status_msg.edit_text(chunks[0], parse_mode="Markdown")
    except Exception:
        try:
            await status_msg.edit_text(chunks[0], parse_mode=None)
        except Exception as e:
            logger.error(f"Failed to edit status message: {e}")

    for chunk in chunks[1:]:
        try:
            await message.reply(chunk, parse_mode="Markdown")
        except Exception:
            await message.reply(chunk, parse_mode=None)


@router.message(F.text & ~F.text.startswith("/"))
async def handle_text_message(message: Message, session: Session, user_settings: UserSetting):
    bot_info = await message.bot.get_me()

    # Check if message is addressed to the bot (always in DM, or via @mention/reply in groups)
    if not is_message_for_bot(message, bot_info.username, bot_info.id):
        return

    raw_text = message.text.strip()
    cleaned_text = clean_bot_mention(raw_text, bot_info.username)

    if not cleaned_text:
        await message.reply("👋 Привет! Чем я могу помочь?", parse_mode="Markdown")
        return

    # Check if this is a reply to another message (context injection)
    prompt_with_context = cleaned_text
    if message.reply_to_message:
        reply_sender = message.reply_to_message.from_user
        sender_name = reply_sender.first_name if reply_sender else "Пользователь"
        reply_text = message.reply_to_message.text or message.reply_to_message.caption or ""
        
        # Only inject if reply is not from the bot itself (bot history is already in session)
        if not reply_sender or reply_sender.id != bot_info.id:
            if reply_text:
                prompt_with_context = f"[В ответ на сообщение от {sender_name}: \"{reply_text[:1000]}\"]\n\n{cleaned_text}"

    # Auto-title on first message
    existing_messages = await db.get_messages(session.id, only_uncompressed=False)
    if session.title == "Новый диалог" and len(existing_messages) == 0:
        asyncio.create_task(auto_title_task(session.id, cleaned_text, session.model))

    await process_chat_generation(
        message=message,
        session=session,
        user_settings=user_settings,
        prompt_text=prompt_with_context,
    )
