"""
FastAPI route definitions.

Endpoints:
  POST /api/chat              — conversational turn
  POST /api/register-event    — direct event registration (used by chatbot internally and for testing)
  GET  /api/events            — list stored events
  GET  /api/events/{id}       — get one event
  DELETE /api/sessions/{id}   — reset a session
"""

from __future__ import annotations

import logging
from typing import Any, Dict

import asyncpg
from fastapi import APIRouter, HTTPException, status
from pydantic import ValidationError

from app.models.event import (
    ChatMessage,
    ChatResponse,
    EventCreate,
    RegisterEventRequest,
)
from app.services.chatbot import chatbot_service
from app.services.database import db_service

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/chat", response_model=ChatResponse, tags=["Chat"])
async def chat(payload: ChatMessage) -> ChatResponse:
    """
    Process one conversational turn.
    If the chatbot has collected all fields and the user confirmed, the event
    is automatically saved to the database.
    """
    response = await chatbot_service.handle_message(payload.session_id, payload.message)

    # Auto-save when the user confirmed
    if chatbot_service.is_completed(payload.session_id) and response.scenario == "success_save":
        draft = chatbot_service.get_draft(payload.session_id)
        if draft:
            try:
                event = EventCreate(**draft)
                await _persist_event(payload.session_id, event)
                response.message = f"✅ Event '{event.name}' saved successfully to the database!"
            except ValidationError as exc:
                response = ChatResponse(
                    scenario="invalid_input",
                    message=f"Validation error: {exc.errors()[0]['msg']}",
                )
            except asyncpg.UniqueViolationError:
                response = ChatResponse(
                    scenario="error_db",
                    message=(
                        "An event with this name and date already exists. "
                        "Please use a different name or date."
                    ),
                )
            except Exception as exc:
                logger.exception("DB error during auto-save: %s", exc)
                response = ChatResponse(
                    scenario="error_db",
                    message="A database error occurred. Please try again.",
                )

    return response


# ── Direct event registration ─────────────────────────────────────────────────


@router.post(
    "/register-event",
    status_code=status.HTTP_201_CREATED,
    tags=["Events"],
)
async def register_event(payload: RegisterEventRequest) -> Dict[str, Any]:
    """
    Directly register a fully-formed event.
    Used for programmatic/API access and unit tests.
    """
    try:
        if await db_service.event_exists(payload.event.name, payload.event.date):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "role": "assistant",
                    "scenario": "error_db",
                    "message": (
                        f"An event named '{payload.event.name}' "
                        f"on {payload.event.date} already exists."
                    ),
                },
            )

        row = await db_service.insert_event(payload.event)
        return {
            "status": "success",
            "message": f"Event '{payload.event.name}' registered successfully.",
            "event_id": row["id"],
        }

    except asyncpg.UniqueViolationError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "role": "assistant",
                "scenario": "error_db",
                "message": "Duplicate event (same name + date).",
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("register_event error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "role": "assistant",
                "scenario": "error_db",
                "message": "Failed to save the event due to a database error.",
            },
        )


# ── Events list / detail ──────────────────────────────────────────────────────


@router.get("/events", tags=["Events"])
async def list_events(limit: int = 50) -> Dict[str, Any]:
    rows = await db_service.get_events(limit)
    return {"events": rows, "count": len(rows)}


@router.get("/events/{event_id}", tags=["Events"])
async def get_event(event_id: int) -> Dict[str, Any]:
    row = await db_service.get_event_by_id(event_id)
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"message": f"Event {event_id} not found."},
        )
    return row


# ── Session management ────────────────────────────────────────────────────────


@router.delete("/sessions/{session_id}", tags=["Sessions"])
async def reset_session(session_id: str) -> Dict[str, str]:
    chatbot_service.reset_session(session_id)
    return {"message": f"Session '{session_id}' has been reset."}


# ── internal helper ───────────────────────────────────────────────────────────


async def _persist_event(session_id: str, event: EventCreate) -> Dict[str, Any]:
    if await db_service.event_exists(event.name, event.date):
        raise asyncpg.UniqueViolationError(
            "Event with same name+date exists",
            constraint_name="events_name_date_key",
        )
    return await db_service.insert_event(event)
