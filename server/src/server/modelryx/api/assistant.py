import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from modelryx.assistant.models import (
    AssistantConversationResponse,
    AssistantMessageRequest,
    AssistantMessageResponse,
    AssistantStoredMessage,
)
from modelryx.assistant.service import AssistantToolGateway, classify_intent
from modelryx.assistant.tool_loop import ToolExecution, run_tool_loop
from modelryx.assistant.tools import EngineeringToolRegistry
from modelryx.core.audit import record_audit, user_for_principal
from modelryx.core.auth import Principal, current_principal
from modelryx.core.config import Settings, get_settings
from modelryx.core.db import get_db
from modelryx.models import AiConversation, AiMessage, AiToolExecution
from modelryx.services.ai import AiGateway

router = APIRouter(prefix="/api/v1/ai/assistant", tags=["ai-assistant"])
Db = Annotated[Session, Depends(get_db)]
Actor = Annotated[Principal, Depends(current_principal)]
Config = Annotated[Settings, Depends(get_settings)]


def owned_conversation(
    db: Session, conversation_id: uuid.UUID, user_id: uuid.UUID
) -> AiConversation:
    conversation = db.get(AiConversation, conversation_id)
    if conversation is None or conversation.created_by != user_id:
        raise HTTPException(status_code=404, detail="Assistant conversation not found")
    return conversation


@router.post(
    "/messages",
    response_model=AssistantMessageResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_assistant_message(
    payload: AssistantMessageRequest,
    request: Request,
    db: Db,
    principal: Actor,
    settings: Config,
) -> AssistantMessageResponse:
    user = user_for_principal(db, principal)
    if payload.conversation_id is None:
        conversation = AiConversation(
            created_by=user.id,
            title=payload.message.strip()[:80],
        )
        db.add(conversation)
        db.flush()
    else:
        conversation = owned_conversation(db, payload.conversation_id, user.id)

    context_snapshot = payload.context.model_dump(mode="json", exclude_none=True)
    user_message = AiMessage(
        conversation_id=conversation.id,
        role="user",
        content=payload.message.strip(),
        context_snapshot_json=context_snapshot,
    )
    db.add(user_message)
    db.flush()

    gateway = AiGateway(settings)
    tools_enabled = settings.ai_tool_calling_enabled and gateway.chat_enabled
    try:
        intent = classify_intent(payload.message, payload.context)
    except HTTPException as exc:
        if not tools_enabled or exc.status_code != 422:
            raise
        intent = "TOOL_ASSISTED"
    warnings = []
    # Keep specialized deterministic capabilities (BOM, viewer, validation,
    # comparisons and feature/material answers) until matching tools are exposed.
    if tools_enabled and intent in {"TOOL_ASSISTED", "ENGINEERING_SEARCH", "ITEM_SUMMARY"}:
        registry = EngineeringToolRegistry(db, principal)
        context = registry.validate_context(payload.context)
        tool_result = run_tool_loop(
            gateway.chat_adapter,
            registry,
            settings,
            payload.message,
            context,
        )
        intent = "TOOL_ASSISTED"
        executions = tool_result.executions
        warnings = tool_result.warnings
    else:
        tool_name, tool_result, duration_ms = AssistantToolGateway(db, principal, settings).execute(
            intent, payload.context, payload.message
        )
        executions = [
            ToolExecution(
                tool_name, context_snapshot, "COMPLETED", duration_ms, tool_result.summary
            )
        ]
        if settings.ai_tool_calling_enabled and not gateway.chat_enabled:
            warnings.append("AI tools are not configured; using the deterministic assistant.")
    trace_id = request.state.request_id
    response = AssistantMessageResponse(
        conversation_id=conversation.id,
        message_id=uuid.uuid4(),
        answer=tool_result.answer,
        intent=intent,
        references=tool_result.references,
        actions=tool_result.actions,
        warnings=warnings,
        trace_id=trace_id,
    )
    assistant_message = AiMessage(
        id=response.message_id,
        conversation_id=conversation.id,
        role="assistant",
        content=response.answer,
        context_snapshot_json=context_snapshot,
        response_json=response.model_dump(mode="json"),
    )
    db.add(assistant_message)
    db.flush()
    for execution in executions:
        db.add(
            AiToolExecution(
                message_id=assistant_message.id,
                tool_name=execution.name,
                arguments_json=execution.arguments,
                status=execution.status,
                duration_ms=execution.duration_ms,
                result_summary_json=execution.summary,
            )
        )
    record_audit(
        db,
        actor_id=user.id,
        action="ai.assistant.message",
        entity_type="ai_conversation",
        entity_id=conversation.id,
        request_id=trace_id,
        details={
            "intent": intent,
            "tool": executions[0].name if len(executions) == 1 else None,
            "tools": [execution.name for execution in executions],
        },
    )
    db.commit()
    return response


@router.get(
    "/conversations/{conversation_id}",
    response_model=AssistantConversationResponse,
)
def read_assistant_conversation(
    conversation_id: uuid.UUID, db: Db, principal: Actor
) -> AssistantConversationResponse:
    user = user_for_principal(db, principal)
    conversation = owned_conversation(db, conversation_id, user.id)
    messages = db.scalars(
        select(AiMessage)
        .where(AiMessage.conversation_id == conversation.id)
        .order_by(AiMessage.created_at, AiMessage.id)
    )
    return AssistantConversationResponse(
        id=conversation.id,
        title=conversation.title,
        messages=[
            AssistantStoredMessage(
                id=message.id,
                role=message.role,
                content=message.content,
                context=message.context_snapshot_json,
                response=message.response_json,
                created_at=message.created_at,
            )
            for message in messages
        ],
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )
