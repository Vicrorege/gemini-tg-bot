"""Command handlers for bot configuration, search settings, and session management."""

from aiogram import Router, F
from aiogram.filters import Command, CommandStart, CommandObject
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from bot.config import config
from bot.db.database import db
from bot.db.models import Session, UserSetting
from bot.llm.compressor import compressor
from bot.handlers.chat import process_chat_generation

router = Router(name="commands")


def build_web_mode_keyboard(current_mode: str) -> InlineKeyboardMarkup:
    modes = [
        ("auto", "🤖 Авто (по смыслу)", "sess:web:auto"),
        ("on", "🟢 Всегда искать в сети", "sess:web:on"),
        ("off", "🔴 Отключить поиск", "sess:web:off"),
    ]
    buttons = []
    for mode_key, title, cb_data in modes:
        prefix = "✅ " if mode_key == current_mode else ""
        buttons.append([InlineKeyboardButton(text=f"{prefix}{title}", callback_data=cb_data)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def build_model_keyboard(current_model: str) -> InlineKeyboardMarkup:
    buttons = []
    for model_name in config.available_models:
        prefix = "✅ " if model_name == current_model else "⚪️ "
        buttons.append([InlineKeyboardButton(text=f"{prefix}{model_name}", callback_data=f"model:set:{model_name}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@router.message(CommandStart())
async def cmd_start(message: Message, session: Session, user_settings: UserSetting):
    web_mode_str = {
        "auto": "🤖 Автоматический (по контексту)",
        "on": "🟢 Всегда включен",
        "off": "🔴 Отключен"
    }.get(user_settings.web_search_mode, "🤖 Автоматический")

    text = (
        "👋 *Привет! Я твой персональный AI-ассистент на базе Gemini с доступом в интернет.*\n\n"
        "✨ *Возможности:*\n"
        "• 🌐 **Поиск в интернете в реальном времени** — актуальные новости, релизы, факты, курсы и документация.\n"
        "• 🔗 **Чтение ссылок и сайтов** — просто отправьте ссылку, и бот изучит её содержимое.\n"
        "• 💬 **Полноценные диалоговые сессии** — раздельные ветки для разных задач.\n"
        "• 🧠 **Умное сжатие контекста** — бот помнит суть разговора даже при больших объемах сообщений.\n"
        "• ⚡️ **Стриминг в реальном времени** — мгновенный вывод ответов.\n"
        "• 🖼 **Мультимодальность** — понимание фото, скриншотов, схем и документов (PDF, код, текст).\n\n"
        "🛠 *Основные команды:*\n"
        "• `/search <запрос>` — Принудительно найти ответ в интернете\n"
        "• `/web` — Настройка режима веб-поиска (авто / включен / выключен)\n"
        "• `/new [название]` — Начать новую сессию диалога\n"
        "• `/sessions` — Список всех сессий и переключение\n"
        "• `/rename <название>` — Переименовать текущую сессию\n"
        "• `/clear` — Очистить историю текущей сессии\n"
        "• `/delete` — Удалить текущую сессию\n"
        "• `/model [название]` — Посмотреть или изменить модель\n"
        "• `/compress` — Принудительно сжать контекст текущей сессии\n"
        "• `/stats` — Статистика токенов и сессии\n"
        "• `/help` — Подробная справка\n\n"
        f"📌 *Текущая сессия:* `{session.title}` (ID: `{session.id}`)\n"
        f"🤖 *Активная модель:* `{session.model or user_settings.selected_model or config.default_model}`\n"
        f"🌐 *Режим веб-поиска:* {web_mode_str}"
    )
    await message.answer(text, parse_mode="Markdown")


@router.message(Command("help"))
async def cmd_help(message: Message):
    text = (
        "📖 *Справка по командам и использованию:*\n\n"
        "🌐 *Веб-поиск и работа с интернетом:*\n"
        "• `/search <запрос>` — Выполняет прямой поиск в интернете и генерирует структурированный ответ со ссылками на источники.\n"
        "• `/web` — Открывает меню настройки поиска в сети (`auto`, `on`, `off`).\n"
        "• **Отправка ссылок:** Если вы прикрепите URL (например, статью на Хабре или репозиторий GitHub), бот автоматически загрузит страницу и проанализирует её.\n\n"
        "🔹 *Управление сессиями:*\n"
        "• `/new [название]` — Создает новую изолированную сессию диалога. Если название не указано, бот сгенерирует его автоматически по смыслу первой фразы.\n"
        "• `/sessions` — Открывает инлайн-меню со всеми сохраненными диалогами для быстрого переключения.\n"
        "• `/rename <текст>` — Меняет название текущего диалога.\n"
        "• `/clear` — Удаляет все сообщения в текущей сессии, сбрасывая контекст.\n"
        "• `/delete` — Полностью удаляет текущую сессию со всей историей.\n\n"
        "🔹 *Модели и сжатие:*\n"
        "• `/model` — Показывает текущую модель и список доступных.\n"
        "• `/model <имя>` — Устанавливает модель для текущей и будущих сессий (например: `agy/gemini-3.7-flash-high` или `agy/gemini-2.5-flash`).\n"
        "• `/compress` — Запускает фоновую суммаризацию истории текущей сессии.\n"
        "• `/stats` — Детальная информация о количестве сообщений, токенов и сжатии.\n\n"
        "🔹 *Файлы и изображения:*\n"
        "• Отправьте **фото/скриншот** с вопросом в подписи — модель распознает изображение.\n"
        "• Отправьте **PDF-документ или файл с кодом** (.py, .json, .md, .txt) — бот прочитает его и ответит на вопросы."
    )
    await message.answer(text, parse_mode="Markdown")


@router.message(Command("search"))
async def cmd_search(message: Message, command: CommandObject, session: Session, user_settings: UserSetting):
    if not command.args or not command.args.strip():
        await message.answer(
            "🔍 *Использование команды /search:*\n\n"
            "`/search <ваш поисковый запрос>`\n\n"
            "Пример:\n"
            "`/search какие главные фичи вышли в Python 3.13?`\n"
            "`/search новости искусственного интеллекта за эту неделю`",
            parse_mode="Markdown"
        )
        return

    query = command.args.strip()
    await process_chat_generation(
        message=message,
        session=session,
        user_settings=user_settings,
        prompt_text=query,
        force_search=True,
    )


@router.message(Command("web"))
async def cmd_web(message: Message, command: CommandObject, user_settings: UserSetting):
    if command.args and command.args.strip():
        arg = command.args.strip().lower()
        if arg in {"auto", "on", "off"}:
            await db.set_user_web_search_mode(message.from_user.id, arg)
            mode_desc = {
                "auto": "🤖 Автоматический (бот сам решает, когда искать по смыслу вопроса)",
                "on": "🟢 Всегда включен (поиск будет выполняться на каждый запрос)",
                "off": "🔴 Отключен (бот отвечает только из собственных знаний)"
            }[arg]
            await message.answer(f"✅ Режим поиска в сети установлен:\n*{mode_desc}*", parse_mode="Markdown")
            return

    cur_mode = user_settings.web_search_mode
    kb = build_web_mode_keyboard(cur_mode)
    await message.answer(
        "🌐 *Настройка поиска информации в интернете:*\n\n"
        "• **Авто (Auto)** — бот автоматически ищет свежие данные, новости, релизы и цены, когда это необходимо.\n"
        "• **Всегда включен (On)** — бот ищет информацию в DuckDuckGo на каждый ваш запрос.\n"
        "• **Отключен (Off)** — поиск выключен, бот использует только внутренние знания модели.",
        reply_markup=kb,
        parse_mode="Markdown"
    )


@router.callback_query(F.data.startswith("sess:web:"))
async def cb_web_mode(callback: CallbackQuery):
    mode = callback.data.split(":")[2]
    user_id = callback.from_user.id
    if mode in {"auto", "on", "off"}:
        await db.set_user_web_search_mode(user_id, mode)
        kb = build_web_mode_keyboard(mode)
        try:
            await callback.message.edit_reply_markup(reply_markup=kb)
        except Exception:
            pass
        mode_names = {"auto": "Автоматический", "on": "Всегда включен", "off": "Отключен"}
        await callback.answer(f"Режим веб-поиска: {mode_names.get(mode, mode)}")


@router.message(Command("new"))
async def cmd_new(message: Message, command: CommandObject):
    title = command.args.strip() if command.args else "Новый диалог"
    user_id = message.from_user.id
    new_sess = await db.create_new_session(user_id=user_id, title=title)
    await message.answer(
        f"✨ *Создана новая сессия:* `{new_sess.title}` (ID: `{new_sess.id}`)\n"
        f"🤖 *Модель:* `{new_sess.model}`\n"
        "Контекст чист. Можете писать сообщение!",
        parse_mode="Markdown"
    )


@router.message(Command("rename"))
async def cmd_rename(message: Message, command: CommandObject, session: Session):
    if not command.args or not command.args.strip():
        await message.answer("⚠️ Укажите новое название: `/rename Мой проект`", parse_mode="Markdown")
        return

    new_title = command.args.strip()[:50]
    await db.update_session_title(session.id, new_title)
    await message.answer(f"✏️ Сессия переименована в: *{new_title}*", parse_mode="Markdown")


@router.message(Command("clear", "reset"))
async def cmd_clear(message: Message, session: Session):
    await db.clear_session_messages(session.id)
    await message.answer(
        f"🧹 История сообщений в сессии *\"{session.title}\"* очищена.",
        parse_mode="Markdown"
    )


@router.message(Command("delete"))
async def cmd_delete(message: Message, session: Session):
    user_id = message.from_user.id
    deleted_title = session.title
    success = await db.delete_session(user_id, session.id)
    if success:
        active_sess = await db.get_or_create_active_session(user_id)
        await message.answer(
            f"🗑 Сессия *\"{deleted_title}\"* удалена.\n"
            f"📌 Активная сессия переключена на: *\"{active_sess.title}\"* (ID: `{active_sess.id}`)",
            parse_mode="Markdown"
        )
    else:
        await message.answer("⚠️ Не удалось удалить сессию.")


@router.message(Command("model"))
async def cmd_model(message: Message, command: CommandObject, session: Session, user_settings: UserSetting):
    cur_model = session.model or user_settings.selected_model or config.default_model

    if not command.args or not command.args.strip():
        kb = build_model_keyboard(cur_model)
        text = (
            f"🤖 *Выбор модели нейросети:*\n\n"
            f"• *Текущая модель:* `{cur_model}`\n\n"
            "Выберите подходящую модель из доступного пула под ваши задачи:"
        )
        await message.answer(text, reply_markup=kb, parse_mode="Markdown")
        return

    new_model = command.args.strip()
    await db.set_user_model(message.from_user.id, new_model)
    session.model = new_model
    await message.answer(f"✅ Модель успешно изменена на: `{new_model}`", parse_mode="Markdown")


@router.callback_query(F.data.startswith("model:set:"))
async def cb_select_model(callback: CallbackQuery, session: Session):
    model_name = callback.data.split("model:set:", 1)[1]
    user_id = callback.from_user.id
    if model_name in config.available_models:
        await db.set_user_model(user_id, model_name)
        session.model = model_name
        kb = build_model_keyboard(model_name)
        try:
            await callback.message.edit_reply_markup(reply_markup=kb)
        except Exception:
            pass
        await callback.answer(f"Модель переключена на: {model_name}")
    else:
        await callback.answer("⚠️ Модель не найдена в пуле.", show_alert=True)


@router.message(Command("compress"))
async def cmd_compress(message: Message, session: Session):
    status_msg = await message.answer("⏳ *Выполняется сжатие контекста диалога...*", parse_mode="Markdown")
    success, desc = await compressor.compress_session(session, protect_last_n=config.protect_last_n_messages)
    icon = "✅" if success else "ℹ️"
    await status_msg.edit_text(f"{icon} {desc}", parse_mode="Markdown")


@router.message(Command("stats", "info"))
async def cmd_stats(message: Message, session: Session, user_settings: UserSetting):
    all_msgs = await db.get_messages(session.id, only_uncompressed=False)
    uncompressed = [m for m in all_msgs if not m.is_compressed]
    compressed_count = len(all_msgs) - len(uncompressed)

    active_tokens = sum(m.tokens for m in uncompressed)
    web_mode_str = {
        "auto": "🤖 Авто",
        "on": "🟢 Всегда включен",
        "off": "🔴 Отключен"
    }.get(user_settings.web_search_mode, "🤖 Авто")

    text = (
        f"📊 *Информация о сессии:*\n\n"
        f"• *ID:* `{session.id}`\n"
        f"• *Название:* `{session.title}`\n"
        f"• *Модель:* `{session.model or user_settings.selected_model or config.default_model}`\n"
        f"• *Режим веб-поиска:* {web_mode_str}\n"
        f"• *Создана:* `{session.created_at}`\n\n"
        f"💬 *Сообщения:*\n"
        f"• Всего сообщений: `{len(all_msgs)}`\n"
        f"• Активных в контексте: `{len(uncompressed)}`\n"
        f"• Сжатых в архив: `{compressed_count}`\n\n"
        f"🧮 *Контекст:*\n"
        f"• Примерно токенов в активном контексте: ~`{active_tokens}`\n"
        f"• Лимит авто-сжатия: `{config.max_context_tokens}` токенов\n"
        f"• Защищенное окно: последние `{config.protect_last_n_messages}` сообщений\n"
        f"• Наличие сжатого резюме: {'Да (' + str(len(session.summary)) + ' симв.)' if session.summary else 'Нет'}"
    )
    await message.answer(text, parse_mode="Markdown")


@router.message(Command("prompt"))
async def cmd_prompt(message: Message, command: CommandObject, user_settings: UserSetting):
    if not command.args or not command.args.strip():
        cur = user_settings.system_prompt or config.system_prompt
        await message.answer(
            f"🧠 *Текущий системный промпт:*\n\n_{cur}_\n\n"
            "Чтобы изменить, отправьте: `/prompt <новый системный промпт>`\n"
            "Чтобы сбросить на стандартный: `/prompt default`",
            parse_mode="Markdown"
        )
        return

    val = command.args.strip()
    if val.lower() == "default":
        val = config.system_prompt

    await db.set_user_system_prompt(message.from_user.id, val)
    await message.answer("✅ Системный промпт успешно обновлен.", parse_mode="Markdown")
