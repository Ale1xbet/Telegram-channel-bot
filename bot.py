import asyncio
import logging
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


async def build_queue_text(page: int = 0):
    pending = await ensure_schedules()
    total = len(pending)
    items = pending[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
    if not items:
        return None, total, items

    lines = ["📥 <b>Очередь публикации</b>"]
    for index, item in enumerate(items):
        queue_index = page * PAGE_SIZE + index
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


async def publish_media(bot: Bot, item):
    file_path = Path(item["file_path"])
    if not file_path.is_file():
        raise FileNotFoundError(f"Файл не найден: {file_path}")

    media = FSInputFile(file_path)
    if file_path.suffix.lower() in VIDEO_EXTENSIONS:
        await bot.send_video(CHANNEL_ID, media)
    else:
        await bot.send_photo(CHANNEL_ID, media)

    await mark_media_sent(item["id"])


async def main():
    if not BOT_TOKEN:
        raise RuntimeError("Укажи BOT_TOKEN в файле .env")

    await init_db()

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()

    @dp.message(Command("start"))
    async def start_handler(message: Message):
        if not is_admin(message):
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
            "В разделе «📥 Очередь» можно посмотреть расписание, открыть предпросмотр, "
            "отправить файл вручную, удалить отдельный файл или очистить очередь.",
            reply_markup=main_keyboard,
        )

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
        file_path = Path(item["file_path"])
        if not file_path.is_file():
            await callback.answer("Файл не найден на диске.", show_alert=True)
            return

        caption = f"{file_path.name}\n🕒 Публикация: {format_post_time(post_time)}"
        media = FSInputFile(file_path)
        if file_path.suffix.lower() in VIDEO_EXTENSIONS:
            await callback.message.answer_video(
                media,
                caption=caption,
                reply_markup=preview_keyboard(media_id),
            )
        else:
            await callback.message.answer_photo(
                media,
                caption=caption,
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
            await publish_media(bot, item)
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
        if not is_admin(message):
            return
        photo = message.photo[-1]
        file_info = await bot.get_file(photo.file_id)
        file_path = MEDIA_DIR / f"{photo.file_unique_id}.jpg"
        await bot.download_file(file_info.file_path, destination=file_path)
        await add_media(str(file_path))
        await message.answer("Фото добавлено в очередь.", reply_markup=main_keyboard)

    @dp.message(F.video)
    async def video_handler(message: Message):
        if not is_admin(message):
            return
        video = message.video
        file_info = await bot.get_file(video.file_id)
        file_path = MEDIA_DIR / f"{video.file_unique_id}.mp4"
        await bot.download_file(file_info.file_path, destination=file_path)
        await add_media(str(file_path))
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
