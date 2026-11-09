import asyncio
import logging
import random
from datetime import datetime, time, timedelta
from pathlib import Path

from aiogram import Bot
from aiogram.types import FSInputFile

from config import (
    CHANNEL_ID,
    MEDIA_DIR,
    POST_WINDOW_END,
    POST_WINDOW_START,
    TIMEZONE,
)
from storage import (
    get_pending_media,
    mark_media_sent,
    set_media_scheduled_time,
)

logger = logging.getLogger(__name__)

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm"}


def _random_time_on_date(day, start: time, end: time) -> datetime:
    start_at = datetime.combine(day, start, tzinfo=TIMEZONE)
    end_at = datetime.combine(day, end, tzinfo=TIMEZONE)
    if end_at <= start_at:
        raise ValueError("Время окончания окна должно быть позже времени начала.")

    seconds = int((end_at - start_at).total_seconds())
    return start_at + timedelta(seconds=random.randint(0, seconds))


def _first_schedule_time(now: datetime) -> datetime:
    today_start = datetime.combine(now.date(), POST_WINDOW_START, tzinfo=TIMEZONE)
    today_end = datetime.combine(now.date(), POST_WINDOW_END, tzinfo=TIMEZONE)

    if now < today_start:
        return _random_time_on_date(now.date(), POST_WINDOW_START, POST_WINDOW_END)

    if now < today_end:
        remaining_start = now.time().replace(tzinfo=None)
        return _random_time_on_date(
            now.date(),
            max(POST_WINDOW_START, remaining_start),
            POST_WINDOW_END,
        )

    tomorrow = now.date() + timedelta(days=1)
    return _random_time_on_date(tomorrow, POST_WINDOW_START, POST_WINDOW_END)


async def ensure_schedules():
    pending = await get_pending_media()
    if not pending:
        return pending

    scheduled = [
        datetime.fromisoformat(item["scheduled_at"])
        for item in pending
        if item["scheduled_at"]
    ]

    if scheduled:
        next_time = max(scheduled) + timedelta(days=1)
    else:
        next_time = _first_schedule_time(datetime.now(TIMEZONE))

    for item in pending:
        if item["scheduled_at"]:
            continue

        await set_media_scheduled_time(item["id"], next_time.isoformat())
        item["scheduled_at"] = next_time.isoformat()
        next_time += timedelta(days=1)

    return pending


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


async def scheduler_loop(bot: Bot):
    while True:
        try:
            pending = await ensure_schedules()
            if not pending:
                await asyncio.sleep(30)
                continue

            first = pending[0]
            post_at = datetime.fromisoformat(first["scheduled_at"])
            delay = (post_at - datetime.now(TIMEZONE)).total_seconds()

            if delay > 0:
                await asyncio.sleep(min(delay, 30))
                continue

            await publish_media(bot, first)
            logger.info(
                "Scheduled media id=%s published to channel %s",
                first["id"],
                CHANNEL_ID,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Scheduled publication failed; will retry in 60 seconds")
            await asyncio.sleep(60)
