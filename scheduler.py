import asyncio
import html
import logging
import random
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

import aiosqlite

from aiogram import Bot
from aiogram.types import FSInputFile

from config import (
    DB_PATH,
    CHANNEL_ID,
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


async def _schedule_regular_items(items, now: datetime):
    scheduled_items = [item for item in items if item["scheduled_at"]]
    scheduled_times = [
        datetime.fromisoformat(item["scheduled_at"])
        for item in scheduled_items
    ]

    # Исправляем старое расписание, где всем постам назначили один час суток.
    legacy_same_time = (
        len(scheduled_times) > 1
        and len({
            value.timetz().replace(tzinfo=None)
            for value in scheduled_times
        }) == 1
    )

    if legacy_same_time:
        logger.warning("Legacy regular schedule detected; rebuilding random times")
        for item in items:
            item["scheduled_at"] = None
            await set_media_scheduled_time(item["id"], "")
        scheduled_times = []

    if scheduled_times:
        next_day = max(value.date() for value in scheduled_times) + timedelta(days=1)
        first_time = None
    else:
        first_time = _first_schedule_time(now)
        next_day = first_time.date()

    for item in items:
        if item["scheduled_at"]:
            continue

        if first_time is not None:
            scheduled_at = first_time
            first_time = None
        else:
            scheduled_at = _random_time_on_date(
                next_day,
                POST_WINDOW_START,
                POST_WINDOW_END,
            )

        await set_media_scheduled_time(item["id"], scheduled_at.isoformat())
        item["scheduled_at"] = scheduled_at.isoformat()
        next_day = scheduled_at.date() + timedelta(days=1)


async def _schedule_proposal_items(items, now: datetime):
    # Учитываем уже опубликованные сегодня предложенные посты.
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT sent_at FROM media WHERE is_proposal = 1 AND sent_at IS NOT NULL"
        )
        sent_rows = await cursor.fetchall()

    used_dates = set()
    for (sent_at,) in sent_rows:
        try:
            sent_utc = datetime.fromisoformat(sent_at).replace(tzinfo=timezone.utc)
            used_dates.add(sent_utc.astimezone(TIMEZONE).date())
        except (TypeError, ValueError):
            logger.warning("Could not parse proposal sent_at value: %r", sent_at)

    # Сохраняем будущие даты, но просроченные и повторяющиеся переносим.
    for item in items:
        if not item["scheduled_at"]:
            continue

        scheduled_at = datetime.fromisoformat(item["scheduled_at"])
        if (
            scheduled_at.date() < now.date()
            or scheduled_at.date() in used_dates
            or (scheduled_at.date() == now.date() and scheduled_at <= now)
        ):
            item["scheduled_at"] = None
            await set_media_scheduled_time(item["id"], "")
        else:
            used_dates.add(scheduled_at.date())

    if items and all(not item["scheduled_at"] for item in items):
        first_time = _first_schedule_time(now)
        next_day = first_time.date()
    elif used_dates:
        first_time = None
        next_day = max(used_dates) + timedelta(days=1)
    else:
        first_time = _first_schedule_time(now)
        next_day = first_time.date()

    for item in items:
        if item["scheduled_at"]:
            continue

        while next_day in used_dates:
            next_day += timedelta(days=1)

        if first_time is not None and next_day == first_time.date():
            scheduled_at = first_time
            first_time = None
        else:
            scheduled_at = _random_time_on_date(
                next_day,
                POST_WINDOW_START,
                POST_WINDOW_END,
            )

        await set_media_scheduled_time(item["id"], scheduled_at.isoformat())
        item["scheduled_at"] = scheduled_at.isoformat()
        used_dates.add(scheduled_at.date())
        next_day = scheduled_at.date() + timedelta(days=1)


async def ensure_schedules():
    pending = await get_pending_media()
    if not pending:
        return pending

    regular_items = [item for item in pending if not item.get("is_proposal")]
    proposal_items = [item for item in pending if item.get("is_proposal")]

    now = datetime.now(TIMEZONE)
    await _schedule_regular_items(regular_items, now)
    await _schedule_proposal_items(proposal_items, now)

    pending.sort(key=lambda item: datetime.fromisoformat(item["scheduled_at"]))
    return pending


async def publish_media(bot: Bot, item, bot_url: str | None = None):
    content_type = item.get("content_type", "photo")
    is_proposal = bool(item.get("is_proposal"))

    suffix = (
        f'\n\n<a href="{html.escape(bot_url, quote=True)}">предложка</a>'
        if is_proposal and bot_url
        else ""
    )

    if content_type == "text":
        text = item["text_content"] or ""
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
                CHANNEL_ID,
                media,
                caption=caption,
                parse_mode="HTML" if is_proposal else None,
            )
        else:
            await bot.send_photo(
                CHANNEL_ID,
                media,
                caption=caption,
                parse_mode="HTML" if is_proposal else None,
            )

    await mark_media_sent(item["id"])


async def scheduler_loop(bot: Bot, bot_url: str | None = None):
    while True:
        try:
            pending = await ensure_schedules()
            if not pending:
                await asyncio.sleep(30)
                continue

            # Берём ближайшую публикацию из обеих независимых очередей.
            first = min(
                pending,
                key=lambda item: datetime.fromisoformat(item["scheduled_at"]),
            )
            post_at = datetime.fromisoformat(first["scheduled_at"])
            delay = (post_at - datetime.now(TIMEZONE)).total_seconds()

            if delay > 0:
                await asyncio.sleep(min(delay, 30))
                continue

            await publish_media(bot, first, bot_url)
            logger.info(
                "Scheduled media id=%s proposal=%s published to channel %s",
                first["id"],
                bool(first.get("is_proposal")),
                CHANNEL_ID,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Scheduled publication failed; will retry in 60 seconds")
            await asyncio.sleep(60)
