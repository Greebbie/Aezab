"""Speech-to-text API."""

from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from server.engine.asr import (
    ASRError,
    ASRService,
    ASRValidationError,
    asr_uses_llm_api_key,
    config_from_settings,
    effective_max_file_mb,
    get_asr_config_metadata,
    save_asr_config_update,
)
from server.middleware.auth import get_current_user
from server.schemas.asr import ASRConfigResponse, ASRConfigUpdate, ASRStatusResponse, ASRTranscriptionResponse

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(get_current_user)])


@router.get("/status", response_model=ASRStatusResponse)
async def asr_status():
    config = config_from_settings()
    return ASRStatusResponse(
        enabled=config.provider != "disabled",
        provider=config.provider,
        base_url=config.base_url,
        model=config.model,
        max_file_mb=effective_max_file_mb(config),
        has_api_key=bool(config.api_key),
        uses_llm_api_key=asr_uses_llm_api_key(config),
    )


def _config_response(config) -> ASRConfigResponse:
    metadata = get_asr_config_metadata()
    return ASRConfigResponse(
        enabled=config.provider != "disabled",
        provider=config.provider,
        base_url=config.base_url,
        model=config.model,
        max_file_mb=effective_max_file_mb(config),
        has_api_key=bool(config.api_key),
        uses_llm_api_key=asr_uses_llm_api_key(config),
        timeout=config.timeout,
        funasr_path=config.funasr_path,
        config_path=metadata["config_path"],
        config_file_exists=metadata["config_file_exists"],
        has_saved_config=metadata["has_saved_config"],
        has_saved_api_key=metadata["has_saved_api_key"],
        api_key_source=metadata["api_key_source"],
    )


@router.get("/config", response_model=ASRConfigResponse)
async def get_asr_config():
    return _config_response(config_from_settings())


@router.put("/config", response_model=ASRConfigResponse)
async def update_asr_config(body: ASRConfigUpdate):
    if body.provider != "disabled" and (not body.base_url or not body.model):
        raise HTTPException(status_code=400, detail="ASR base URL and model are required")
    config = save_asr_config_update(body.model_dump())
    return _config_response(config)


@router.post("/transcribe", response_model=ASRTranscriptionResponse)
async def transcribe_audio(
    file: UploadFile = File(...),
    language: str | None = Form(None),
    prompt: str | None = Form(None),
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="Audio filename is required")

    audio_bytes = await file.read()
    service = ASRService()
    try:
        result = await service.transcribe(
            filename=file.filename,
            content_type=file.content_type or "application/octet-stream",
            audio_bytes=audio_bytes,
            language=language,
            prompt=prompt,
        )
    except ASRValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        logger.warning("ASR provider HTTP error: %s", exc)
        body = exc.response.text[:500]
        raise HTTPException(
            status_code=502,
            detail=f"ASR provider returned HTTP {exc.response.status_code}: {body}",
        ) from exc
    except (httpx.RequestError, ASRError) as exc:
        logger.warning("ASR provider failed: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return ASRTranscriptionResponse(
        text=result.text,
        provider=result.provider,
        model=result.model,
        language=result.language,
        duration_seconds=result.duration_seconds,
        raw=result.raw,
    )
