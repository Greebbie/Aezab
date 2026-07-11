"""Tool management API — register, update, test tools."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.db import get_db
from server.models.tool import ToolDefinition
from server.schemas.tool import ToolCreate, ToolUpdate, ToolOut, ToolTestRequest, ToolTestResponse
from server.engine.tool_gateway import ToolGateway
from server.middleware.auth import get_current_user, get_tenant_id
from server.api._usage_check import get_resource_usage

router = APIRouter(dependencies=[Depends(get_current_user)])


@router.get("/", response_model=list[ToolOut])
async def list_tools(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ToolDefinition).where(ToolDefinition.tenant_id == tenant_id))
    return result.scalars().all()


@router.post("/", response_model=ToolOut, status_code=201)
async def create_tool(
    body: ToolCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
):
    tool = ToolDefinition(
        name=body.name,
        description=body.description,
        category=body.category,
        endpoint=body.endpoint,
        method=body.method,
        input_schema=body.input_schema,
        output_schema=body.output_schema,
        auth_config=body.auth_config,
        timeout_ms=body.timeout_ms,
        max_retries=body.max_retries,
        retry_backoff_ms=body.retry_backoff_ms,
        is_async=body.is_async,
        callback_url=body.callback_url,
        required_permission=body.required_permission,
        risk_level=body.risk_level,
        tenant_id=tenant_id,
    )
    db.add(tool)
    await db.commit()
    await db.refresh(tool)
    return tool


@router.get("/{tool_id}", response_model=ToolOut)
async def get_tool(
    tool_id: str, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ToolDefinition).where(ToolDefinition.id == tool_id, ToolDefinition.tenant_id == tenant_id)
    )
    tool = result.scalar_one_or_none()
    if not tool:
        raise HTTPException(404, "Tool not found")
    return tool


@router.get("/{tool_id}/usage")
async def get_tool_usage(
    tool_id: str, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
):
    """Report which agents currently depend on this tool."""
    result = await db.execute(
        select(ToolDefinition).where(ToolDefinition.id == tool_id, ToolDefinition.tenant_id == tenant_id)
    )
    if not result.scalar_one_or_none():
        raise HTTPException(404, "Tool not found")
    return await get_resource_usage(
        db, tenant_id,
        skill_type="tool_call",
        matches=lambda ec: tool_id in (ec.get("tool_ids") or []),
    )


@router.put("/{tool_id}", response_model=ToolOut)
async def update_tool(
    tool_id: str,
    body: ToolUpdate,
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ToolDefinition).where(ToolDefinition.id == tool_id, ToolDefinition.tenant_id == tenant_id)
    )
    tool = result.scalar_one_or_none()
    if not tool:
        raise HTTPException(404, "Tool not found")

    update_data = body.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(tool, key, value)

    await db.commit()
    await db.refresh(tool)
    return tool


@router.delete("/{tool_id}", status_code=204)
async def delete_tool(
    tool_id: str, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ToolDefinition).where(ToolDefinition.id == tool_id, ToolDefinition.tenant_id == tenant_id)
    )
    tool = result.scalar_one_or_none()
    if not tool:
        raise HTTPException(404, "Tool not found")
    await db.delete(tool)
    await db.commit()


@router.post("/test", response_model=ToolTestResponse)
async def test_tool(
    body: ToolTestRequest,
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    """Test a tool's connectivity and basic invocation."""
    result = await db.execute(
        select(ToolDefinition).where(
            ToolDefinition.id == body.tool_id, ToolDefinition.tenant_id == tenant_id
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(404, "Tool not found")

    gw = ToolGateway(db)
    result = await gw.test_connectivity(body.tool_id, body.test_input)
    return ToolTestResponse(
        tool_id=body.tool_id,
        success=result["success"],
        response=result.get("response", result.get("status_code")),
        error=result.get("error"),
        latency_ms=result.get("latency_ms", 0),
    )
