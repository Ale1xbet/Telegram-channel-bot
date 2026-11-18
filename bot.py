import asyncio
import html
import json
import logging
import uuid
from pathlib import Path
from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    FSInputFile,
)

from config import (
    BOT_TOKEN,
    CHANNEL_ID,
    MEDIA_DIR,
    POST_WINDOW_START,
    POST_WINDOW_END,
    TIMEZONE,
)
from scheduler import scheduler_loop, ensure_schedules
from storage import (
    add_media,
    count_pending_media,
    get_pending_media,
    get_pending_media_by_id,
    init_db,
    mark_media_sent,
    delete_pending_media,
    clear_pending_media,
    add_text_media,
    create_proposal,
    get_pending_proposal,
    set_proposal_status,
)

ADMIN_ID = 963194894
PAGE_SIZE = 8
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm"}

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📥 Очередь"), KeyboardButton(text="⚙️ Настройки")],
        [KeyboardButton(text="ℹ️ Помощь")],
    ],
    resize_keyboard=True,
)


def is_admin(message: Message) -> bool:
    return message.from_user is not None and message.from_user.id == ADMIN_ID


def format_post_time(post_at) -> str:
    return post_at.strftime("%d.%m.%Y в %H:%M")


def queue_keyboard(items, page: int, total: int):
    rows = []
    for index, item in enumerate(items):
        rows.append([
            InlineKeyboardButton(
                text=f"👁 Просмотреть {index + 1 + page * PAGE_SIZE}",
                callback_data=f"preview:{item['id']}",
            )
        ])

    navigation = []
    if page > 0:
        navigation.append(
            InlineKeyboardButton(text="⬅️ Назад", callback_data=f"queue:{page - 1}")
        )
    if (page + 1) * PAGE_SIZE < total:
        navigation.append(
            InlineKeyboardButton(text="Вперёд ➡️", callback_data=f"queue:{page + 1}")
        )
    if navigation:
        rows.append(navigation)

    if total:
        rows.append([
            InlineKeyboardButton(
                text="🗑 Очистить очередь",
                callback_data="clear:confirm",
            )
        ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def preview_keyboard(media_id: int):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🚀 Отправить сейчас",
                    callback_data=f"send_now:{media_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🗑 Удалить из очереди",
                    callback_data=f"delete:confirm:{media_id}",
                ),
            ],
        ]
    )


def delete_confirmation_keyboard(media_id: int):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да, удалить",
                    callback_data=f"delete:yes:{media_id}",
                ),
                InlineKeyboardButton(
                    text="Отмена",
                    callback_data=f"delete:no:{media_id}",
                ),
            ]
        ]
    )


def clear_confirmation_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да, очистить",
                    callback_data="clear:yes",
                ),
                InlineKeyboardButton(
                    text="Отмена",
                    callback_data="clear:no",
                ),
            ]
        ]
    )


def proposal_keyboard(proposal_id: int):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Одобрить",
                    callback_data=f"proposal:approve:{proposal_id}",
                ),
                InlineKeyboardButton(
                    text="❌ Отклонить",
                    callback_data=f"proposal:reject:{proposal_id}",
                ),
            ]
        ]
    )


async def build_queue_text(page: int = 0):
    pending = await ensure_schedules()
    total = len(pending)
    items = pending[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
    if not items:
        return None, total, items

    lines = ["📥 <b>Очередь публикации</b>"]
    for index, item in enumerate(items):
        queue_index = page * PAGE_SIZE + index
        if item.get("content_type") == "text":
            name = (item.get("text_content") or "Текстовый пост").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            name = name[:45] + ("…" if len(name) > 45 else "")
        else:
            name = Path(item["file_path"]).name
        post_time = datetime.fromisoformat(item["scheduled_at"])
        lines.append(
            f"{queue_index + 1}. <code>{name}</code> — 🕒 {format_post_time(post_time)}"
        )

    return "\n".join(lines), total, items


async def send_queue(message: Message, page: int = 0):
    text, total, items = await build_queue_text(page)
    if total == 0:
        await message.answer("Очередь пока пуста.", reply_markup=main_keyboard)
        return

    await message.answer(
        text,
        reply_markup=queue_keyboard(items, page, total),
        parse_mode="HTML",
    )


async def publish_media(bot: Bot, item, bot_url: str | None = None):
    content_type = item.get("content_type", "photo")
    is_proposal = bool(item.get("is_proposal"))
    suffix = (
        f'\n\n<a href="{html.escape(bot_url, quote=True)}">предложка</a>'
        if is_proposal and bot_url
        else ""
    )

    if content_type == "text":
        text = item["text_content"]
        if is_proposal:
            text = html.escape(text) + suffix
            await bot.send_message(CHANNEL_ID, text, parse_mode="HTML")
        else:
            await bot.send_message(CHANNEL_ID, text)
    else:
        file_path = Path(item["file_path"])
        if not file_path.is_file():
            raise FileNotFoundError(f"Файл не найден: {file_path}")

        media = FSInputFile(file_path)
        caption = item.get("caption")
        if is_proposal:
            caption = html.escape(caption or "") + suffix
        if content_type == "video" or file_path.suffix.lower() in VIDEO_EXTENSIONS:
            await bot.send_video(
                CHANNEL_ID, media, caption=caption,
                parse_mode="HTML" if is_proposal else None,
            )
        else:
            await bot.send_photo(
                CHANNEL_ID, media, caption=caption,
                parse_mode="HTML" if is_proposal else None,
            )

    await mark_media_sent(item["id"])


async def main():
    if not BOT_TOKEN:
        raise RuntimeError("Укажи BOT_TOKEN в файле .env")

    await init_db()

    bot = Bot(token=BOT_TOKEN)
    bot_info = await bot.get_me()
    bot_url = f"https://t.me/{bot_info.username}" if bot_info.username else None
    dp = Dispatcher()

    async def forward_proposal(message: Message, content_type: str, content: dict):
        user = message.from_user
        proposal_id = await create_proposal(
            content_type,
            json.dumps(content, ensure_ascii=False),
            user.id,
            user.full_name,
        )
        try:
            await bot.copy_message(
                chat_id=ADMIN_ID,
                from_chat_id=message.chat.id,
                message_id=message.message_id,
                reply_markup=proposal_keyboard(proposal_id),
            )
            await bot.send_message(
                ADMIN_ID,
                f"📨 Предложение №{proposal_id} от {user.full_name} "
                f"(ID: {user.id}).",
            )
        except TelegramAPIError:
            await set_proposal_status(proposal_id, "delivery_failed")
            logger.exception("Could not forward proposal id=%s to admin", proposal_id)
            await message.answer("Не удалось передать предложение администратору. Попробуй позже.")
            return
        await message.answer("Спасибо! Предложение отправлено на модерацию.")

    @dp.message(Command("start"))
    async def start_handler(message: Message):
        if not is_admin(message):
            await message.answer(
                "Привет! Здесь можно предложить публикацию для канала. "
                "Отправь текст, фото или видео с подписью — администратор проверит предложение."
            )
            return
        await message.answer(
            "Привет! Выбери действие в меню или отправь фото/видео для очереди.",
            reply_markup=main_keyboard,
        )

    @dp.message(Command("settings"))
    @dp.message(F.text == "⚙️ Настройки")
    async def settings_handler(message: Message):
        if not is_admin(message):
            return
        queue_size = await count_pending_media()
        await message.answer(
            f"Канал: {CHANNEL_ID}\n"
            f"Окно публикации: {POST_WINDOW_START.strftime('%H:%M')}–"
            f"{POST_WINDOW_END.strftime('%H:%M')}\n"
            f"Часовой пояс: {TIMEZONE}\n"
            f"Файлов в очереди: {queue_size}",
            reply_markup=main_keyboard,
        )

    @dp.message(F.text == "📥 Очередь")
    async def queue_handler(message: Message):
        if not is_admin(message):
            return
        await send_queue(message)

    @dp.message(F.text == "ℹ️ Помощь")
    async def help_handler(message: Message):
        if not is_admin(message):
            return
        await message.answer(
            "Отправь фото или видео — оно попадёт в очередь. "
            "Подписчики могут присылать боту текст, фото или видео с подписью на модерацию. "
            "В разделе «📥 Очередь» можно посмотреть расписание, открыть предпросмотр, "
            "отправить публикацию вручную, удалить отдельную публикацию или очистить очередь.",
            reply_markup=main_keyboard,
        )

    @dp.message(F.text)
    async def text_proposal_handler(message: Message):
        if is_admin(message) or message.chat.type != "private":
            return
        if message.text.startswith("/"):
            return
        await forward_proposal(message, "text", {"text": message.text})

    @dp.callback_query(F.data.startswith("proposal:"))
    async def proposal_decision_handler(callback: CallbackQuery):
        if callback.from_user.id != ADMIN_ID:
            await callback.answer("Нет доступа.", show_alert=True)
            return

        _, decision, raw_id = callback.data.split(":")
        proposal_id = int(raw_id)
        proposal = await get_pending_proposal(proposal_id)
        if not proposal:
            await callback.answer("Предложение уже обработано.", show_alert=True)
            return

        if decision == "reject":
            await set_proposal_status(proposal_id, "rejected")
            try:
                await bot.send_message(
                    proposal["submitter_id"],
                    "Спасибо за предложение! К сожалению, в этот раз оно не подойдёт для публикации.",
                )
            except TelegramAPIError:
                logger.info("Could not notify submitter for rejected proposal %s", proposal_id)
            await callback.message.edit_reply_markup(reply_markup=None)
            await callback.answer("Предложение отклонено.")
            return

        try:
            content = json.loads(proposal["content"])
            if proposal["content_type"] == "text":
                await add_text_media(content["text"], is_proposal=True)
            else:
                file_info = await bot.get_file(content["file_id"])
                extension = ".mp4" if proposal["content_type"] == "video" else ".jpg"
                file_path = MEDIA_DIR / f"proposal_{proposal_id}_{uuid.uuid4().hex}{extension}"
                await bot.download_file(file_info.file_path, destination=file_path)
                await add_media(
                    str(file_path),
                    caption=content.get("caption"),
                    content_type=proposal["content_type"],
                    is_proposal=True,
                )

            await set_proposal_status(proposal_id, "approved")
            try:
                await bot.send_message(
                    proposal["submitter_id"],
                    "Спасибо! Твоё предложение одобрено и добавлено в очередь публикации.",
                )
            except TelegramAPIError:
                logger.info("Could not notify submitter for approved proposal %s", proposal_id)

            await callback.message.edit_reply_markup(reply_markup=None)
            await callback.answer("Одобрено и добавлено в очередь.")
        except Exception:
            logger.exception("Could not approve proposal id=%s", proposal_id)
            await callback.answer("Не удалось добавить предложение в очередь.", show_alert=True)

    @dp.callback_query(F.data.startswith("queue:"))
    async def queue_page_handler(callback: CallbackQuery):
        if callback.from_user.id != ADMIN_ID:
            await callback.answer("Нет доступа.", show_alert=True)
            return

        page = int(callback.data.split(":")[1])
        text, total, items = await build_queue_text(page)
        if not items:
            await callback.message.edit_text("Очередь пуста.")
            await callback.answer()
            return

        max_page = max((total - 1) // PAGE_SIZE, 0)
        page = min(page, max_page)
        text, total, items = await build_queue_text(page)
        await callback.message.edit_text(
            text,
            reply_markup=queue_keyboard(items, page, total),
            parse_mode="HTML",
        )
        await callback.answer()

    @dp.callback_query(F.data == "clear:confirm")
    async def clear_confirm_handler(callback: CallbackQuery):
        if callback.from_user.id != ADMIN_ID:
            await callback.answer("Нет доступа.", show_alert=True)
            return
        await callback.message.edit_text(
            "Удалить все файлы из очереди? Это действие нельзя отменить.",
            reply_markup=clear_confirmation_keyboard(),
        )
        await callback.answer()

    @dp.callback_query(F.data == "clear:yes")
    async def clear_yes_handler(callback: CallbackQuery):
        if callback.from_user.id != ADMIN_ID:
            await callback.answer("Нет доступа.", show_alert=True)
            return
        removed = await clear_pending_media()
        await callback.message.edit_text(f"Очередь очищена. Удалено файлов: {removed}.")
        await callback.answer("Готово.")

    @dp.callback_query(F.data == "clear:no")
    async def clear_no_handler(callback: CallbackQuery):
        if callback.from_user.id != ADMIN_ID:
            await callback.answer("Нет доступа.", show_alert=True)
            return
        await callback.message.edit_text("Очистка отменена.")
        await callback.answer()

    @dp.callback_query(F.data.startswith("delete:confirm:"))
    async def delete_confirm_handler(callback: CallbackQuery):
        if callback.from_user.id != ADMIN_ID:
            await callback.answer("Нет доступа.", show_alert=True)
            return

        media_id = int(callback.data.rsplit(":", 1)[1])
        item = await get_pending_media_by_id(media_id)
        if not item:
            await callback.answer("Файл уже отправлен или отсутствует.", show_alert=True)
            return

        if item.get("content_type") == "text":
            await callback.message.edit_text(
                f"Удалить текстовый пост из очереди?\n\n{item['text_content'][:500]}",
                reply_markup=delete_confirmation_keyboard(media_id),
            )
        else:
            await callback.message.edit_caption(
                caption=f"Удалить файл «{Path(item['file_path']).name}» из очереди?",
                reply_markup=delete_confirmation_keyboard(media_id),
            )
        await callback.answer()

    @dp.callback_query(F.data.startswith("delete:yes:"))
    async def delete_yes_handler(callback: CallbackQuery):
        if callback.from_user.id != ADMIN_ID:
            await callback.answer("Нет доступа.", show_alert=True)
            return

        media_id = int(callback.data.rsplit(":", 1)[1])
        removed = await delete_pending_media(media_id)
        if removed:
            if callback.message.text:
                await callback.message.edit_text("🗑 Пост удалён из очереди.", reply_markup=None)
            else:
                await callback.message.edit_caption(
                    caption="🗑 Файл удалён из очереди и с диска.",
                    reply_markup=None,
                )
            await callback.answer("Удалено.")
        else:
            await callback.answer("Файл уже отправлен или отсутствует.", show_alert=True)

    @dp.callback_query(F.data.startswith("delete:no:"))
    async def delete_no_handler(callback: CallbackQuery):
        if callback.from_user.id != ADMIN_ID:
            await callback.answer("Нет доступа.", show_alert=True)
            return

        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.answer("Удаление отменено.")

    @dp.callback_query(F.data.startswith("preview:"))
    async def preview_handler(callback: CallbackQuery):
        if callback.from_user.id != ADMIN_ID:
            await callback.answer("Нет доступа.", show_alert=True)
            return

        media_id = int(callback.data.split(":")[1])
        item = await get_pending_media_by_id(media_id)
        if not item:
            await callback.answer("Файл уже отправлен или отсутствует.", show_alert=True)
            return

        pending = await ensure_schedules()
        item = next((queued for queued in pending if queued["id"] == media_id), None)
        if not item:
            await callback.answer("Файл уже отправлен или отсутствует.", show_alert=True)
            return

        post_time = datetime.fromisoformat(item["scheduled_at"])
        if item.get("content_type") == "text":
            preview_text = (
                f"📝 Текстовый пост\n🕒 Публикация: {format_post_time(post_time)}\n\n"
                f"{item['text_content']}"
            )
            if item.get("is_proposal"):
                preview_text = html.escape(preview_text)
                if bot_url:
                    preview_text += (
                        f'\n\n<a href="{html.escape(bot_url, quote=True)}">предложка</a>'
                    )
            await callback.message.answer(
                preview_text,
                parse_mode="HTML" if item.get("is_proposal") else None,
                reply_markup=preview_keyboard(media_id),
            )
        else:
            file_path = Path(item["file_path"])
            if not file_path.is_file():
                await callback.answer("Файл не найден на диске.", show_alert=True)
                return

            caption = (
                f"{file_path.name}\n🕒 Публикация: {format_post_time(post_time)}"
                + (f"\n\n{item['caption']}" if item.get("caption") else "")
            )
            if item.get("is_proposal"):
                caption = html.escape(caption)
                if bot_url:
                    caption += (
                        f'\n\n<a href="{html.escape(bot_url, quote=True)}">предложка</a>'
                    )
            media = FSInputFile(file_path)
            if item.get("content_type") == "video" or file_path.suffix.lower() in VIDEO_EXTENSIONS:
                await callback.message.answer_video(
                    media,
                    caption=caption,
                    parse_mode="HTML" if item.get("is_proposal") else None,
                    reply_markup=preview_keyboard(media_id),
                )
            else:
                await callback.message.answer_photo(
                    media,
                    caption=caption,
                    parse_mode="HTML" if item.get("is_proposal") else None,
                    reply_markup=preview_keyboard(media_id),
                )
        await callback.answer()

    @dp.callback_query(F.data.startswith("send_now:"))
    async def send_now_handler(callback: CallbackQuery):
        if callback.from_user.id != ADMIN_ID:
            await callback.answer("Нет доступа.", show_alert=True)
            return

        media_id = int(callback.data.split(":")[1])
        item = await get_pending_media_by_id(media_id)
        if not item:
            await callback.answer("Файл уже отправлен или отсутствует.", show_alert=True)
            return

        await callback.answer("Отправляю в канал…")
        try:
            await publish_media(bot, item, bot_url)
        except (TelegramAPIError, OSError, FileNotFoundError) as error:
            logger.exception("Manual publication failed for media id=%s", media_id)
            error_text = str(error)[:180]
            await callback.message.answer(
                f"❌ Не удалось отправить в канал.\nПричина: {error_text}"
            )
            return
        except Exception:
            logger.exception("Unexpected manual publication error for media id=%s", media_id)
            await callback.message.answer(
                "❌ Неожиданная ошибка при отправке. Подробности записаны в лог."
            )
            return

        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramAPIError:
            pass
        await callback.message.answer("✅ Файл опубликован в канале.")
        logger.info("Media id=%s manually published to channel %s", media_id, CHANNEL_ID)

    @dp.message(F.photo)
    async def photo_handler(message: Message):
        photo = message.photo[-1]
        if not is_admin(message):
            if message.chat.type != "private":
                return
            await forward_proposal(
                message,
                "photo",
                {"file_id": photo.file_id, "caption": message.caption},
            )
            return

        file_info = await bot.get_file(photo.file_id)
        file_path = MEDIA_DIR / f"{photo.file_unique_id}_{uuid.uuid4().hex}.jpg"
        await bot.download_file(file_info.file_path, destination=file_path)
        await add_media(str(file_path), caption=message.caption, content_type="photo")
        await message.answer("Фото добавлено в очередь.", reply_markup=main_keyboard)

    @dp.message(F.video)
    async def video_handler(message: Message):
        video = message.video
        if not is_admin(message):
            if message.chat.type != "private":
                return
            await forward_proposal(
                message,
                "video",
                {"file_id": video.file_id, "caption": message.caption},
            )
            return

        file_info = await bot.get_file(video.file_id)
        file_path = MEDIA_DIR / f"{video.file_unique_id}_{uuid.uuid4().hex}.mp4"
        await bot.download_file(file_info.file_path, destination=file_path)
        await add_media(str(file_path), caption=message.caption, content_type="video")
        await message.answer("Видео добавлено в очередь.", reply_markup=main_keyboard)

    scheduler_task = asyncio.create_task(scheduler_loop(bot))
    try:
        await dp.start_polling(bot)
    finally:
        scheduler_task.cancel()
        await asyncio.gather(scheduler_task, return_exceptions=True)
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
