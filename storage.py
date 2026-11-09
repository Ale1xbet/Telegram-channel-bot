import aiosqlite
from pathlib import Path

from config import DB_PATH, MEDIA_DIR


async def init_db():
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS media (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT NOT NULL UNIQUE,
                added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                sent_at TEXT,
                scheduled_at TEXT
            )
            """
        )

        cursor = await db.execute("PRAGMA table_info(media)")
        columns = {row[1] for row in await cursor.fetchall()}
        if "scheduled_at" not in columns:
            await db.execute("ALTER TABLE media ADD COLUMN scheduled_at TEXT")

        await db.commit()


async def add_media(file_path: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO media (file_path) VALUES (?)",
            (file_path,),
        )
        await db.commit()


async def get_pending_media():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT id, file_path, scheduled_at
            FROM media
            WHERE sent_at IS NULL
            ORDER BY id
            """
        )
        return [dict(row) for row in await cursor.fetchall()]


async def get_pending_media_page(limit: int, offset: int = 0):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT id, file_path, scheduled_at FROM media
            WHERE sent_at IS NULL
            ORDER BY id
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )
        return [dict(row) for row in await cursor.fetchall()]


async def get_pending_media_by_id(media_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT id, file_path, scheduled_at FROM media
            WHERE id = ? AND sent_at IS NULL
            """,
            (media_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def set_media_scheduled_time(media_id: int, scheduled_at: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE media
            SET scheduled_at = ?
            WHERE id = ? AND sent_at IS NULL
            """,
            (scheduled_at, media_id),
        )
        await db.commit()


async def mark_media_sent(media_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE media SET sent_at = CURRENT_TIMESTAMP WHERE id = ?",
            (media_id,),
        )
        await db.commit()


async def count_pending_media() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM media WHERE sent_at IS NULL"
        )
        row = await cursor.fetchone()
        return row[0]


async def delete_pending_media(media_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT file_path FROM media WHERE id = ? AND sent_at IS NULL",
            (media_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return False

        file_path = Path(row["file_path"])
        await db.execute(
            "DELETE FROM media WHERE id = ? AND sent_at IS NULL",
            (media_id,),
        )
        await db.commit()

    try:
        file_path.unlink(missing_ok=True)
    except OSError:
        pass

    return True


async def clear_pending_media() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT file_path FROM media WHERE sent_at IS NULL"
        )
        file_paths = [row["file_path"] for row in await cursor.fetchall()]

        await db.execute("DELETE FROM media WHERE sent_at IS NULL")
        await db.commit()

    for file_path in file_paths:
        try:
            Path(file_path).unlink(missing_ok=True)
        except OSError:
            pass

    return len(file_paths)
