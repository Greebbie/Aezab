"""Zero-config local backup — zips the SQLite database, FAISS vector index,
and local config files into ``./data/backups/`` on a schedule, with no
operator action required.

Self-hosted single-process deployments (see docs/deployment.md) have no
managed-database snapshotting, so an accidental ``rm -rf ./data`` or a
failing disk means total data loss with no recovery path. This module gives
every self-deployed install an automatic, restorable backup out of the box:

- ``create_backup()`` — one-shot synchronous backup (callers on the async
  event loop must wrap it in ``asyncio.to_thread``, matching the convention
  used by ``server/engine/summary_scheduler.py`` for other blocking work).
- ``backup_scheduler_loop()`` — background task wired into ``server/main.py``
  lifespan; sleeps ``settings.backup_interval_hours`` hours between runs.
- ``list_backups()`` / ``resolve_backup_path()`` — read-side helpers for
  ``server/api/backup.py``.

SQLite is backed up via the stdlib Online Backup API (``sqlite3.Connection.
backup``) rather than a plain file copy: the database runs in WAL mode (see
``server/db.py``), so a naive ``shutil.copy`` of the main ``.db`` file can
miss committed data still sitting in the ``-wal`` file, or copy a file mid
write. The Online Backup API talks to SQLite's own consistent-snapshot
machinery instead.

PostgreSQL deployments are NOT backed up by this module — only the FAISS
index and local config files are collected, and the manifest records
``db_type: "postgresql"`` so operators know to run ``pg_dump`` separately
(see docs/deployment.md).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from server.config import settings

logger = logging.getLogger(__name__)

# Kept in sync with the FastAPI app version in server/main.py — there is no
# shared single source of truth for it today, so this is duplicated rather
# than imported (importing server.main here would pull in the whole app).
APP_VERSION = "0.1.0"

BACKUP_NAME_RE = re.compile(r"^aezab-backup-[0-9]{8}-[0-9]{6}\.zip$")

BACKUPS_DIR = Path("./data/backups")


def _sqlite_path_from_url(database_url: str) -> Path | None:
    """Resolve the filesystem path from a `sqlite[+driver]://...` URL.

    Handles both the relative form (`sqlite:///./data/aezab.db`, 3 slashes)
    and the absolute form (`sqlite:////abs/path.db`, 4 slashes). Returns
    None for non-sqlite URLs or `:memory:` databases (nothing to back up).
    """
    if not database_url.startswith("sqlite"):
        return None

    marker = "://"
    idx = database_url.find(marker)
    if idx == -1:
        return None

    remainder = database_url[idx + len(marker):]
    if remainder.startswith("//"):
        # 4-slash absolute form: keep one leading slash.
        path_str = remainder[1:]
    else:
        # 3-slash relative form: the sole remaining leading slash is a
        # separator, not part of the path.
        path_str = remainder.lstrip("/")

    if not path_str or path_str == ":memory:":
        return None
    return Path(path_str)


def _backup_sqlite_db(db_path: Path, dest_dir: Path) -> Path | None:
    """Copy `db_path` into `dest_dir` using SQLite's Online Backup API.

    Safe to call against a live WAL-mode database with concurrent readers/
    writers — SQLite handles the consistent snapshot internally. Returns the
    path to the backup copy, or None if the source file does not exist.
    """
    if not db_path.exists():
        return None

    dest_path = dest_dir / db_path.name
    src_conn = sqlite3.connect(str(db_path))
    try:
        dst_conn = sqlite3.connect(str(dest_path))
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()
    return dest_path


def _restore_text(db_type: str) -> str:
    """Bilingual RESTORE.txt contents bundled into every backup zip."""
    pg_note_en = (
        "PostgreSQL:\n"
        "This backup does NOT include your PostgreSQL database — only the\n"
        "FAISS vector index and local config files are included. Restore the\n"
        "database separately with pg_dump/pg_restore (see docs/deployment.md),\n"
        "then follow steps 1-4 below for the remaining files.\n\n"
    ) if db_type != "sqlite" else ""
    pg_note_zh = (
        "PostgreSQL 用户：\n"
        "本备份 **不包含** PostgreSQL 数据库，只包含 FAISS 向量索引和本地配置\n"
        "文件。数据库请单独用 pg_dump / pg_restore 恢复（见 docs/deployment.md），\n"
        "数据库恢复完成后再按下面 1-4 步处理本压缩包里的其余文件。\n\n"
    ) if db_type != "sqlite" else ""

    return (
        "RESTORE INSTRUCTIONS / 恢复说明\n"
        "================================\n\n"
        "English:\n"
        "1. Stop the Aezab server process.\n"
        "2. Extract this zip. It contains a `data/` folder with the database\n"
        "   file (SQLite only), the FAISS vector index (if present),\n"
        "   `asr_config.json`, and `secret_key` — whichever of these existed\n"
        "   at backup time.\n"
        "3. Copy the extracted `data/` folder's contents into your\n"
        "   deployment's `./data/` directory, overwriting the existing files.\n"
        "4. Restart the Aezab server.\n\n"
        f"{pg_note_en}"
        "--------------------------------\n\n"
        "中文：\n"
        "1. 停止 Aezab 服务进程。\n"
        "2. 解压本 zip。里面有一个 data/ 目录，包含数据库文件（仅 SQLite）、\n"
        "   FAISS 向量索引（如有）、asr_config.json 和 secret_key ——\n"
        "   具体以备份当时实际存在的文件为准。\n"
        "3. 把解压出来的 data/ 目录内容复制到你部署环境的 ./data/ 目录下，\n"
        "   覆盖已有文件。\n"
        "4. 重启 Aezab 服务。\n\n"
        f"{pg_note_zh}"
    )


def _enforce_retention(backups_dir: Path) -> None:
    """Keep only the newest `settings.backup_keep` backups, oldest first."""
    keep = settings.backup_keep
    if keep <= 0:
        return

    files = sorted(backups_dir.glob("aezab-backup-*.zip"))
    if len(files) <= keep:
        return

    for stale in files[: len(files) - keep]:
        try:
            stale.unlink()
        except OSError as exc:
            logger.warning("backup: failed to remove old backup %s: %s", stale, exc)


def create_backup() -> dict[str, Any]:
    """Create a new backup zip under ./data/backups/ and enforce retention.

    Synchronous and filesystem/CPU-bound (sqlite3.backup + zip compression);
    callers on the asyncio event loop MUST wrap this in `asyncio.to_thread`
    (see server/api/backup.py and backup_scheduler_loop below).

    Returns {name, size_bytes, created_at, contents}.
    """
    created_at = datetime.now(timezone.utc)
    # The filename timestamp uses the server's LOCAL time (same instant as
    # `created_at`, just converted to local wall-clock via astimezone())
    # rather than raw UTC — this matches what the console UI shows in the
    # "Created At" column (`new Date(created_at).toLocaleString()` renders
    # `created_at`'s UTC instant in the browser's local timezone). Backing
    # the filename with bare UTC digits made e.g. `...-060820.zip` look like
    # a different time than the "14:08:20" the UI displayed for the same
    # backup, which was confusing. `created_at` itself stays UTC-aware for
    # the manifest/API payload — only the filename's digits change.
    local_created_at = created_at.astimezone()
    timestamp = local_created_at.strftime("%Y%m%d-%H%M%S")

    backups_dir = BACKUPS_DIR
    backups_dir.mkdir(parents=True, exist_ok=True)

    zip_name = f"aezab-backup-{timestamp}.zip"
    zip_path = backups_dir / zip_name

    is_sqlite = settings.database_url.startswith("sqlite")
    db_type = "sqlite" if is_sqlite else "postgresql"
    contents: list[str] = []

    with tempfile.TemporaryDirectory(prefix="aezab-backup-") as tmp_str:
        tmp_dir = Path(tmp_str)

        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            if is_sqlite:
                sqlite_path = _sqlite_path_from_url(settings.database_url)
                if sqlite_path is not None:
                    backed_up = _backup_sqlite_db(sqlite_path, tmp_dir)
                    if backed_up is not None:
                        arcname = f"data/{sqlite_path.name}"
                        zf.write(backed_up, arcname)
                        contents.append(arcname)

            # FAISS index + sidecar
            faiss_index_path = Path(settings.faiss_index_path)
            if faiss_index_path.is_file():
                arcname = f"data/vectors/{faiss_index_path.name}"
                zf.write(faiss_index_path, arcname)
                contents.append(arcname)

            faiss_sidecar_path = Path(str(faiss_index_path) + ".ids.json")
            if faiss_sidecar_path.is_file():
                arcname = f"data/vectors/{faiss_sidecar_path.name}"
                zf.write(faiss_sidecar_path, arcname)
                contents.append(arcname)

            # Any other index files living in the FAISS index directory
            # (e.g. sharded/multi-domain indexes) not already collected above.
            faiss_dir = Path(settings.faiss_index_dir)
            if faiss_dir.is_dir():
                already = {Path(c).name for c in contents}
                for entry in sorted(faiss_dir.iterdir()):
                    if not entry.is_file() or entry.name in already:
                        continue
                    arcname = f"data/vectors/{entry.name}"
                    zf.write(entry, arcname)
                    contents.append(arcname)
                    already.add(entry.name)

            # ASR config
            asr_config_path = Path(settings.asr_config_path)
            if asr_config_path.is_file():
                arcname = "data/asr_config.json"
                zf.write(asr_config_path, arcname)
                contents.append(arcname)

            # JWT secret key
            secret_key_path = Path("./data/secret_key")
            if secret_key_path.is_file():
                arcname = "data/secret_key"
                zf.write(secret_key_path, arcname)
                contents.append(arcname)

            manifest = {
                "created_at": created_at.isoformat(),
                "app_version": APP_VERSION,
                "db_type": db_type,
                "contents": contents,
            }
            zf.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
            zf.writestr("RESTORE.txt", _restore_text(db_type))

    _enforce_retention(backups_dir)

    return {
        "name": zip_name,
        "size_bytes": zip_path.stat().st_size,
        "created_at": created_at.isoformat(),
        "contents": contents,
    }


def list_backups() -> list[dict[str, Any]]:
    """List existing backups, newest first."""
    backups_dir = BACKUPS_DIR
    if not backups_dir.is_dir():
        return []

    entries: list[dict[str, Any]] = []
    for path in backups_dir.glob("aezab-backup-*.zip"):
        if not BACKUP_NAME_RE.match(path.name):
            continue
        stat = path.stat()
        entries.append({
            "name": path.name,
            "size_bytes": stat.st_size,
            "created_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        })

    entries.sort(key=lambda e: e["name"], reverse=True)
    return entries


def resolve_backup_path(name: str) -> Path:
    """Resolve `name` to a path inside BACKUPS_DIR, rejecting anything that
    is not an exact `aezab-backup-YYYYMMDD-HHMMSS.zip` filename — this is
    the only thing standing between a user-supplied name and path traversal
    (`../../etc/passwd`, absolute paths, etc.), so the regex match must run
    BEFORE any filesystem join.

    Raises FileNotFoundError if the name is invalid or the file is missing.
    """
    if not BACKUP_NAME_RE.match(name):
        raise FileNotFoundError(f"Invalid backup name: {name!r}")

    path = BACKUPS_DIR / name
    if not path.is_file():
        raise FileNotFoundError(f"Backup not found: {name!r}")
    return path


async def backup_scheduler_loop() -> None:
    """Background task: run create_backup() every `backup_interval_hours`.

    Wired into server/main.py's lifespan via asyncio.create_task and
    cancelled on shutdown. Never raises — a failed backup attempt is logged
    and the loop continues so a transient failure (e.g. disk full for one
    run) doesn't silently disable all future backups. Returns immediately,
    without starting the loop, when backup_interval_hours <= 0 (disabled).
    """
    interval_hours = settings.backup_interval_hours
    if interval_hours <= 0:
        logger.info("backup: scheduler disabled (backup_interval_hours <= 0)")
        return

    interval_seconds = interval_hours * 3600
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            result = await asyncio.to_thread(create_backup)
            logger.info(
                "backup: scheduled backup created: %s (%d bytes)",
                result["name"], result["size_bytes"],
            )
        except Exception as exc:  # noqa: BLE001 - background loop must never die
            logger.error("backup: scheduled backup failed: %s", exc, exc_info=True)
