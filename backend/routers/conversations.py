"""Conversation lifecycle: open a session, and read back a turn's RAG context."""

import asyncio
import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException

from backend import container
from backend.conversation import conversations, get_conversation_or_hydrate

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/api/conversation")
async def create_conversation():
    """Create a new conversation session."""
    conversation_id = uuid.uuid4().hex
    welcome = "¡Hola! Soy Mikel, desarrollador. Pregúntame sobre mi experiencia, proyectos o habilidades."

    now_iso = datetime.utcnow().isoformat()
    conversations[conversation_id] = {
        "id": conversation_id,
        "messages": [],
        "turns": [],
        "summary": "",  # Rolling summary of older turns (for memory beyond recent_count)
        "created_at": now_iso,
        "last_activity_at": now_iso,
    }
    logger.info("Created conversation: %s", conversation_id)

    # Write-through: persist the creation immediately (spec: Conversation
    # creation persists). Failures are swallowed inside the service.
    await asyncio.to_thread(
        container.persistence().record_conversation,
        conversation_id,
        "",
        now_iso,
        now_iso,
    )

    return {
        "conversation_id": conversation_id,
        "welcome_message": welcome,
    }


@router.get("/api/conversation/{conversation_id}/context")
async def get_conversation_context(conversation_id: str, turn: int = 0):
    """Return the RAG chunks used for a specific conversation turn."""
    await get_conversation_or_hydrate(conversation_id)

    conv = conversations[conversation_id]
    turns = conv.get("turns", [])
    matching_turn = next((t for t in turns if t["n"] == turn), None)

    if matching_turn is None:
        raise HTTPException(status_code=404, detail=f"Turn {turn} not found")

    return matching_turn.get("chunks_used", [])
