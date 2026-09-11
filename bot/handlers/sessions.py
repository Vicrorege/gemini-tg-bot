"""Handlers for interactive session listing, switching, and management via inline keyboards."""

import math
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from bot.db.database import db
from bot.db.models import Session

router = Router(name="sessions")

PAGE_SIZE = 5


def build_sessions_keyboard(sessions: list[Session], total_count: int, offset: int, active_session_id: int) -> InlineKeyboardMarkup:
    buttons = []
    current_page = (offset // PAGE_SIZE) + 1
    total_pages = max(1, math.ceil(total_count / PAGE_SIZE))

    # List session buttons
    for s in sessions:
        is_current = s.id == active_session_id
        icon = "🟢" if is_current else "⚪️"
        btn_text = f"{icon} {s.title[:30]}"
        buttons.append([InlineKeyboardButton(text=btn_text, callback_data=f"sess:switch:{s.id}:{offset}")])

    # Navigation row
    nav_row = []
    if offset >= PAGE_SIZE:
        nav_row.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"sess:list:{offset - PAGE_SIZE}"))
    nav_row.append(InlineKeyboardButton(text=f"{current_page}/{total_pages}", callback_data="sess:noop"))
    if offset + PAGE_SIZE < total_count:
        nav_row.append(InlineKeyboardButton(text="Вперед ➡️", callback_data=f"sess:list:{offset + PAGE_SIZE}"))
    if nav_row:
        buttons.append(nav_row)

    # Action row
    buttons.append([
        InlineKeyboardButton(text="➕ Новый диалог", callback_data="sess:create"),
        InlineKeyboardButton(text="🗑 Удалить текущий", callback_data=f"sess:delete:{active_session_id}:{offset}")
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


@router.message(Command("sessions"))
async def cmd_sessions(message: Message, session: Session):
    user_id = message.from_user.id
    sessions, total = await db.get_user_sessions(user_id, limit=PAGE_SIZE, offset=0)
    kb = build_sessions_keyboard(sessions, total, 0, session.id)

    await message.answer(
        "🗂 *Ваши диалоговые сессии:*\n"
        "Нажмите на сессию, чтобы переключиться на неё.",
        reply_markup=kb,
        parse_mode="Markdown"
    )


@router.callback_query(F.data.startswith("sess:list:"))
async def cb_list_sessions(callback: CallbackQuery, session: Session):
    offset = int(callback.data.split(":")[2])
    user_id = callback.from_user.id
    sessions, total = await db.get_user_sessions(user_id, limit=PAGE_SIZE, offset=offset)
    kb = build_sessions_keyboard(sessions, total, offset, session.id)

    await callback.message.edit_reply_markup(reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("sess:switch:"))
async def cb_switch_session(callback: CallbackQuery):
    parts = callback.data.split(":")
    target_id = int(parts[2])
    offset = int(parts[3]) if len(parts) > 3 else 0
    user_id = callback.from_user.id

    success = await db.switch_active_session(user_id, target_id)
    if success:
        target_sess = await db.get_session(target_id)
        sessions, total = await db.get_user_sessions(user_id, limit=PAGE_SIZE, offset=offset)
        kb = build_sessions_keyboard(sessions, total, offset, target_id)
        await callback.message.edit_reply_markup(reply_markup=kb)
        await callback.answer(f"Переключено на: {target_sess.title if target_sess else target_id}")
    else:
        await callback.answer("⚠️ Сессия не найдена.", show_alert=True)


@router.callback_query(F.data == "sess:create")
async def cb_create_session(callback: CallbackQuery):
    user_id = callback.from_user.id
    new_sess = await db.create_new_session(user_id=user_id)
    sessions, total = await db.get_user_sessions(user_id, limit=PAGE_SIZE, offset=0)
    kb = build_sessions_keyboard(sessions, total, 0, new_sess.id)

    await callback.message.edit_text(
        f"✨ *Создан новый диалог:* `{new_sess.title}` (ID: `{new_sess.id}`)\n"
        "Вы можете сразу написать сообщение в чат.",
        reply_markup=kb,
        parse_mode="Markdown"
    )
    await callback.answer("Новая сессия создана!")


@router.callback_query(F.data.startswith("sess:delete:"))
async def cb_delete_session(callback: CallbackQuery):
    parts = callback.data.split(":")
    target_id = int(parts[2])
    offset = int(parts[3]) if len(parts) > 3 else 0
    user_id = callback.from_user.id

    target = await db.get_session(target_id)
    title = target.title if target else f"#{target_id}"

    await db.delete_session(user_id, target_id)
    active_sess = await db.get_or_create_active_session(user_id)
    sessions, total = await db.get_user_sessions(user_id, limit=PAGE_SIZE, offset=0)
    kb = build_sessions_keyboard(sessions, total, 0, active_sess.id)

    await callback.message.edit_text(
        f"🗑 Сессия *\"{title}\"* удалена.\n"
        f"📌 Активная сессия: *\"{active_sess.title}\"*",
        reply_markup=kb,
        parse_mode="Markdown"
    )
    await callback.answer("Сессия удалена")


@router.callback_query(F.data == "sess:noop")
async def cb_noop(callback: CallbackQuery):
    await callback.answer()
