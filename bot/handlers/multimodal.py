"""Handlers for multimodal inputs: photos, documents, and replied media."""

import asyncio
import base64
import json
import logging
import time
from typing import Any, Dict, List

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
from bot.services.search_router import search_router
from bot.services.web_search import web_search_service
from bot.utils.document_parser import parse_document_content
from bot.utils.telegram_format import clean_bot_mention, is_message_for_bot, split_message_text

logger = logging.getLogger(__name__)

router = Router(name="multimodal")


async def stream_and_respond(
    message: Message,
    session: Session,
    user_settings: UserSetting,
    user_payload: Any,
    db_content_storage: str,
    text_query_for_search: str = "",
):
    """Generic helper to stream response for multimodal messages with optional web grounding."""
    await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
    status_msg = await message.reply("👁 *Анализирую данные и генерирую ответ...*", parse_mode="Markdown")

    # Optional search if text prompt requests external info
    search_results = []
    page_contents = []
    grounded_context = ""

    if text_query_for_search and len(text_query_for_search) > 5:
        try:
            decision = await search_router.analyze(text_query_for_search, user_settings=user_settings)
            if decision.target_urls:
                for url in decision.target_urls[:2]:
                    page_data = await web_search_service.fetch_url_content(url)
                    if page_data:
                        page_contents.append(page_data)

            if decision.need_search and decision.queries:
                search_results = await web_search_service.multi_search(decision.queries, max_per_query=2)

            if search_results or page_contents:
                grounded_context, _ = grounding_service.build_grounded_context(search_results, page_contents)
        except Exception as e:
            logger.debug(f"Search skipped in multimodal: {e}")

    base_system_prompt = user_settings.system_prompt or config.system_prompt
    effective_system_prompt = grounding_service.build_system_prompt(base_system_prompt, grounded_context)
    effective_model = session.model or user_settings.selected_model or config.default_model

    messages = await compressor.prepare_context(
        session=session,
        system_prompt=effective_system_prompt,
        new_user_message=user_payload,
    )

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
                except Exception:
                    pass

    except Exception as e:
        logger.exception(f"Multimodal generation error: {e}")
        full_response += f"\n\n⚠️ *Ошибка при обработке: {str(e)}*"

    if not full_response.strip():
        full_response = "*(Модель вернула пустой ответ)*"

    # Save to database
    user_tokens = estimate_tokens(user_payload)
    assistant_tokens = estimate_tokens(full_response)
    await db.add_message(session.id, role="user", content=db_content_storage, tokens=user_tokens)
    await db.add_message(session.id, role="assistant", content=full_response, tokens=assistant_tokens)

    # Render final output
    chunks = split_message_text(full_response, max_length=4000)
    try:
        await status_msg.edit_text(chunks[0], parse_mode="Markdown")
    except Exception:
        try:
            await status_msg.edit_text(chunks[0], parse_mode=None)
        except Exception as e:
            logger.error(f"Failed to edit multimodal response: {e}")

    for chunk in chunks[1:]:
        try:
            await message.reply(chunk, parse_mode="Markdown")
        except Exception:
            await message.reply(chunk, parse_mode=None)


@router.message(F.photo)
async def handle_photo(message: Message, session: Session, user_settings: UserSetting):
    """Handle image / photo inputs with Vision."""
    bot_info = await message.bot.get_me()
    if not is_message_for_bot(message, bot_info.username, bot_info.id):
        return

    photo = message.photo[-1]  # Highest resolution
    raw_caption = message.caption or ""
    clean_caption = clean_bot_mention(raw_caption, bot_info.username) or "Опиши это изображение или ответь на вопросы по нему."

    file_io = await message.bot.download(photo.file_id)
    if not file_io:
        await message.reply("⚠️ Не удалось скачать изображение.")
        return

    img_bytes = file_io.read()
    b64_img = base64.b64encode(img_bytes).decode("utf-8")

    user_payload: List[Dict[str, Any]] = [
        {"type": "text", "text": clean_caption},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"}}
    ]

    db_storage = json.dumps([
        {"type": "text", "text": f"[Изображение / Фото]: {clean_caption}"}
    ], ensure_ascii=False)

    await stream_and_respond(
        message=message,
        session=session,
        user_settings=user_settings,
        user_payload=user_payload,
        db_content_storage=db_storage,
        text_query_for_search=clean_caption
    )


@router.message(F.document)
async def handle_document(message: Message, session: Session, user_settings: UserSetting):
    """Handle text, code, or PDF documents."""
    bot_info = await message.bot.get_me()
    if not is_message_for_bot(message, bot_info.username, bot_info.id):
        return

    doc = message.document
    filename = doc.file_name or "document.txt"
    raw_caption = message.caption or ""
    clean_caption = clean_bot_mention(raw_caption, bot_info.username) or "Проанализируй этот файл."

    if doc.file_size and doc.file_size > 20 * 1024 * 1024:
        await message.reply("⚠️ Файл слишком большой. Максимальный размер: 20 МБ.")
        return

    file_io = await message.bot.download(doc.file_id)
    if not file_io:
        await message.reply("⚠️ Не удалось скачать файл.")
        return

    file_bytes = file_io.read()
    parsed_text = parse_document_content(file_bytes, filename)

    if not parsed_text:
        await message.reply(
            f"⚠️ Не удалось извлечь текст из файла `{filename}`. "
            "Поддерживаются текстовые файлы, код (.py, .js, .json, .md) и текстовые PDF.",
            parse_mode="Markdown"
        )
        return

    combined_prompt = f"{clean_caption}\n\n{parsed_text}"
    await stream_and_respond(
        message=message,
        session=session,
        user_settings=user_settings,
        user_payload=combined_prompt,
        db_content_storage=combined_prompt,
        text_query_for_search=clean_caption
    )


@router.message(F.voice)
async def handle_voice(message: Message, session: Session, user_settings: UserSetting):
    """Handle voice messages."""
    bot_info = await message.bot.get_me()
    if not is_message_for_bot(message, bot_info.username, bot_info.id):
        return

    await message.reply(
        "🎙 *Голосовое сообщение получено.*\n"
        "Для транскрибации голоса отправьте текстовый запрос.",
        parse_mode="Markdown"
    )
