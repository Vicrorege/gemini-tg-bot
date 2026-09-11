"""Handlers for multimodal inputs: photos, documents, media groups (albums), and voice."""

import asyncio
import base64
import json
import logging
import time
from typing import Any, Dict, List, Optional

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
    items_count: int = 1,
):
    """Generic helper to stream response for multimodal messages with optional web grounding."""
    await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
    status_text = (
        f"👁 *Анализирую медиагруппу ({items_count} файлов) и генерирую ответ...*"
        if items_count > 1
        else "👁 *Анализирую данные и генерирую ответ...*"
    )
    status_msg = await message.reply(status_text, parse_mode="Markdown")

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


async def process_media_items(
    primary_message: Message,
    session: Session,
    user_settings: UserSetting,
    items: List[Message],
):
    """Process a batch of media messages (single item or mediagroup album) into one unified prompt."""
    bot_info = await primary_message.bot.get_me()

    # Check if addressed to bot (always true in DM)
    is_for_bot = any(is_message_for_bot(m, bot_info.username, bot_info.id) for m in items)
    if not is_for_bot:
        return

    # Extract non-empty captions across the media group
    raw_captions = [m.caption for m in items if m.caption]
    cleaned_captions = [clean_bot_mention(c, bot_info.username) for c in raw_captions if c]
    clean_caption = "\n".join(cleaned_captions).strip()

    has_photos = any(m.photo for m in items)
    has_documents = any(m.document for m in items)

    if not clean_caption:
        if has_photos and has_documents:
            clean_caption = "Опиши эти изображения и документы или ответь на вопросы по ним."
        elif has_photos:
            clean_caption = "Опиши эти изображения или ответь на вопросы по ним."
        else:
            clean_caption = "Проанализируй эти документы."

    user_payload: List[Dict[str, Any]] = [{"type": "text", "text": clean_caption}]
    download_tasks = []

    # Download and encode all photos and documents concurrently
    for m in items:
        if m.photo:
            photo = m.photo[-1]
            download_tasks.append(("photo", photo.file_id, None))
        elif m.document:
            doc = m.document
            if not doc.file_size or doc.file_size <= 20 * 1024 * 1024:
                download_tasks.append(("document", doc.file_id, doc.file_name or "document.txt"))

    for kind, file_id, filename in download_tasks:
        try:
            file_io = await primary_message.bot.download(file_id)
            if not file_io:
                continue

            file_bytes = file_io.read()
            if kind == "photo":
                b64_img = base64.b64encode(file_bytes).decode("utf-8")
                user_payload.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"}
                })
            elif kind == "document":
                parsed_text = parse_document_content(file_bytes, filename)
                if parsed_text:
                    user_payload.append({
                        "type": "text",
                        "text": f"\n\n[Документ: {filename}]\n{parsed_text}"
                    })
        except Exception as e:
            logger.error(f"Error downloading/parsing media {kind} {file_id}: {e}")

    # Build DB content description
    if len(items) > 1:
        storage_title = f"[Медиагруппа: {len(items)} файлов]: {clean_caption}"
    elif has_photos:
        storage_title = f"[Изображение / Фото]: {clean_caption}"
    else:
        storage_title = f"[Документ]: {clean_caption}"

    db_storage = json.dumps([{"type": "text", "text": storage_title}], ensure_ascii=False)

    await stream_and_respond(
        message=primary_message,
        session=session,
        user_settings=user_settings,
        user_payload=user_payload,
        db_content_storage=db_storage,
        text_query_for_search=clean_caption,
        items_count=len(items)
    )


@router.message(F.photo)
async def handle_photo(
    message: Message,
    session: Session,
    user_settings: UserSetting,
    album: Optional[List[Message]] = None,
):
    """Handle image inputs: single photo or photo album (mediagroup)."""
    items = album if album else [message]
    await process_media_items(message, session, user_settings, items)


@router.message(F.document)
async def handle_document(
    message: Message,
    session: Session,
    user_settings: UserSetting,
    album: Optional[List[Message]] = None,
):
    """Handle document inputs: single document or document album (mediagroup)."""
    items = album if album else [message]
    await process_media_items(message, session, user_settings, items)


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
