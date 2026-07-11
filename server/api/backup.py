"""Backup management API — list/create/download/delete the zip backups
produced by server/engine/backup.py.

Admin-only for the whole router: a backup zip embeds the full local
database — including third-party LLM/Embedding/ASR credentials stored in
plaintext (see docs/deployment.md "已知限制") — plus the JWT `secret_key`,
so anyone who can list or download backups can fully impersonate the
deployment. `require_role("admin")` mirrors the same gate used on
`/auth/api-keys` (server/api/auth.py); in `disable_auth` (dev) mode
`get_current_user` returns a mock admin user, so this never blocks local
development.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from server.engine.backup import create_backup, list_backups, resolve_backup_path
from server.middleware.auth import require_role

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_role("admin"))])


@router.get("/")
async def get_backups() -> list[dict[str, Any]]:
    return await asyncio.to_thread(list_backups)


@router.post("/", status_code=201)
async def post_backup() -> dict[str, Any]:
    try:
        return await asyncio.to_thread(create_backup)
    except Exception as exc:
        logger.error("backup: manual backup failed: %s", exc, exc_info=True)
        raise HTTPException(500, f"Backup failed: {exc}")


@router.get("/{name}/download")
async def download_backup(name: str) -> FileResponse:
    try:
        path = await asyncio.to_thread(resolve_backup_path, name)
    except FileNotFoundError:
        raise HTTPException(404, "Backup not found")
    return FileResponse(path, filename=path.name, media_type="application/zip")


@router.delete("/{name}", status_code=204)
async def delete_backup(name: str):
    try:
        path = await asyncio.to_thread(resolve_backup_path, name)
    except FileNotFoundError:
        raise HTTPException(404, "Backup not found")
    await asyncio.to_thread(path.unlink)
