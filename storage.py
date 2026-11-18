import aiosqlite
import uuid
from pathlib import Path

from config import DB_PATH, MEDIA_DIR


async def init_db():
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS media (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT UNIQUE,
                content_type TEXT NOT NULL DEFAULT 'photo',
                caption TEXT,
                text_content TEXT,
                is_proposal INTEGER NOT NULL DEFAULT 0,
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
        if "content_type" not in columns:
            await db.execute("ALTER TABLE media ADD COLUMN content_type TEXT NOT NULL DEFAULT 'photo'")
        if "caption" not in columns:
            await db.execute("ALTER TABLE media ADD COLUMN caption TEXT")
        if "text_content" not in columns:
            await db.execute("ALTER TABLE media ADD COLUMN text_content TEXT")
        if "is_proposal" not in columns:
            await db.execute("ALTER TABLE media ADD COLUMN is_proposal INTEGER NOT NULL DEFAULT 0")

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS proposals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content_type TEXT NOT NULL,
                content TEXT NOT NULL,
                submitter_id INTEGER NOT NULL,
                submitter_name TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await db.commit()


async def add_media(
    file_path: str,
    caption: str | None = None,
    content_type: str = "photo",
    is_proposal: bool = False,
):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT OR IGNORE INTO media
            (file_path, content_type, caption, is_proposal)
            VALUES (?, ?, ?, ?)
            """,
            (file_path, content_type, caption, int(is_proposal)),
        )
        await db.commit()


async def add_text_media(text_content: str, is_proposal: bool = False):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO media (file_path, content_type, text_content, is_proposal)
            VALUES (?, 'text', ?, ?)
            """,
            (f"TEXT:{uuid.uuid4().hex}", text_content, int(is_proposal)),
        )
        await db.commit()


async def create_proposal(
    content_type: str,
    content: str,
    submitter_id: int,
    submitter_name: str,
) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            INSERT INTO proposals (content_type, content, submitter_id, submitter_name)
            VALUES (?, ?, ?, ?)
            """,
            (content_type, content, submitter_id, submitter_name),
        )
        await db.commit()
        return cursor.lastrowid


async def get_pending_proposal(proposal_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT id, content_type, content, submitter_id, submitter_name
            FROM proposals
            WHERE id = ? AND status = 'pending'
            """,
            (proposal_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def set_proposal_status(proposal_id: int, status: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE proposals SET status = ? WHERE id = ? AND status = 'pending'",
            (status, proposal_id),
        )
        await db.commit()


async def get_pending_media():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT id, file_path, scheduled_at, content_type, caption, text_content, is_proposal
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
            SELECT id, file_path, scheduled_at, content_type, caption, text_content, is_proposal FROM media
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
            SELECT id, file_path, scheduled_at, content_type, caption, text_content, is_proposal FROM media
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
            "SELECT file_path, content_type FROM media WHERE id = ? AND sent_at IS NULL",
            (media_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return False

        file_path = Path(row["file_path"]) if row["content_type"] != "text" else None
        await db.execute(
            "DELETE FROM media WHERE id = ? AND sent_at IS NULL",
            (media_id,),
        )
        await db.commit()

    if file_path is not None:
        try:
            file_path.unlink(missing_ok=True)
        except OSError:
            pass

    return True


async def clear_pending_media() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT file_path, content_type FROM media WHERE sent_at IS NULL"
        )
        rows = await cursor.fetchall()
        file_paths = [
            row["file_path"] for row in rows
            if row["content_type"] != "text"
        ]

        await db.execute("DELETE FROM media WHERE sent_at IS NULL")
        await db.commit()

    for file_path in file_paths:
        try:
            Path(file_path).unlink(missing_ok=True)
        except OSError:
            pass

    return len(rows)
