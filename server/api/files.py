"""Workflow file-upload API — external-app integration surface.

Clients upload a file here first, then place the returned `reference`
string ("file://{file_id}") into a workflow's `form_data` for
`field_type="file"` collect steps. Files are stored on disk under
data/uploads/{tenant_id}/{uuid}{ext}; the uuid filename prevents path
traversal, and the tenant segment (always derived from the authenticated
caller, never client input) provides per-tenant isolation on download.
"""

from __future__ import annotations

import logging
import mimetypes
import os
import re
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from server.config import settings
from server.middleware.auth import get_current_user, get_tenant_id

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(get_current_user)])

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".pdf"}
UPLOAD_ROOT = Path("data") / "uploads"
_FILE_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def _max_upload_bytes() -> int:
    return max(settings.max_upload_mb, 1) * 1024 * 1024


def _sanitize_extension(filename: str) -> str:
    """Return the lowercased extension, rejecting anything not allowlisted."""
    ext = os.path.splitext(os.path.basename(filename))[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            400,
            f"Unsupported file type '{ext}'. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )
    return ext


def _tenant_upload_dir(tenant_id: str) -> Path:
    """Directory for a tenant's uploads, created on demand.

    tenant_id always comes from get_tenant_id (the authenticated caller),
    never from client-controlled input, so this cannot be used to escape
    UPLOAD_ROOT.
    """
    directory = UPLOAD_ROOT / tenant_id
    directory.mkdir(parents=True, exist_ok=True)
    return directory


@router.post("/upload", status_code=201)
async def upload_file(
    file: UploadFile = File(...),
    tenant_id: str = Depends(get_tenant_id),
):
    """Upload a file for use as a workflow file-field value."""
    if not file.filename:
        raise HTTPException(400, "Filename is required")

    ext = _sanitize_extension(file.filename)

    max_bytes = _max_upload_bytes()
    raw_bytes = await file.read(max_bytes + 1)
    if len(raw_bytes) > max_bytes:
        raise HTTPException(
            413, f"File is too large. Max upload size is {settings.max_upload_mb} MB",
        )
    if not raw_bytes:
        raise HTTPException(400, "Uploaded file is empty")

    file_id = uuid.uuid4().hex
    dest_dir = _tenant_upload_dir(tenant_id)
    dest_path = dest_dir / f"{file_id}{ext}"
    dest_path.write_bytes(raw_bytes)

    content_type = file.content_type or mimetypes.guess_type(dest_path.name)[0] or "application/octet-stream"

    logger.info("Stored upload %s (%d bytes) for tenant %s", dest_path.name, len(raw_bytes), tenant_id)

    return {
        "file_id": file_id,
        "filename": os.path.basename(file.filename),
        "size": len(raw_bytes),
        "content_type": content_type,
        "reference": f"file://{file_id}",
    }


@router.get("/{file_id}")
async def download_file(
    file_id: str,
    tenant_id: str = Depends(get_tenant_id),
):
    """Download a previously uploaded file. Tenant-scoped; 404 on mismatch."""
    if not _FILE_ID_RE.match(file_id):
        raise HTTPException(400, "Invalid file id")

    dest_dir = _tenant_upload_dir(tenant_id)
    matches = sorted(dest_dir.glob(f"{file_id}.*"))
    if not matches:
        raise HTTPException(404, "File not found")

    path = matches[0]
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type, filename=path.name)
