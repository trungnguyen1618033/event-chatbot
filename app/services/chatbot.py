from __future__ import annotations

import json
import logging
import re
from copy import deepcopy
from datetime import date, time
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import ValidationError

from app.config import get_settings
from app.models.event import ChatResponse, EventCreate
from app.services.vector_store import vector_store

logger = logging.getLogger(__name__)


# Required fields (must be present before we can save)
REQUIRED_FIELDS = [
    "name",
    "date",
    "time",
    "seat_types",
    "ticket_limit",
    "purchase_start",
    "purchase_end",
    "venue_name",
    "venue_address",
    "capacity",
    "organizer_name",
    "organizer_email",
    "category",
]

FIELD_PROMPTS: Dict[str, str] = {
    "name": "What is the name of your event?",
    "date": "What is the event date? (YYYY-MM-DD)",
    "time": "What time does it start? (HH:MM in 24-hour format)",
    "description": "Could you provide a brief description of the event?",
    "seat_types": "What seat types are available and their prices? (e.g., VIP: 10000, Regular: 5000)",
    "ticket_limit": "What is the maximum number of tickets one person can purchase?",
    "purchase_start": "When does ticket sales open? (YYYY-MM-DD)",
    "purchase_end": "When does ticket sales close? (YYYY-MM-DD)",
    "venue_name": "What is the venue name?",
    "venue_address": "What is the venue address?",
    "capacity": "What is the total seating capacity of the venue?",
    "organizer_name": "What is the organizer's name or company?",
    "organizer_email": "What is the organizer's contact email?",
    "category": "What category does this event fall under? (e.g., Concert, Conference, Festival)",
    "language": "What language will the event be conducted in?",
    "is_recurring": "Is this a recurring event? (Yes/No)",
    "recurrence_frequency": "How often does it recur? (e.g., weekly, monthly)",
    "is_online": "Will this event be online or in-person?",
}

EXTRACTION_SYSTEM_PROMPT = """
You are a helpful event-creation assistant. Your job is to extract structured event
information from the user's message and return ONLY a JSON object.

Current known event draft (may be partial):
{draft}

The user was just asked for: {asked_field}
If the user's message is a single bare value (a date, time, number, name, or short
phrase) and does NOT explicitly name other fields, assign it to "{asked_field}".
Only assign to other fields when the user explicitly mentions them by name.
Do NOT re-emit fields that are already in the draft unchanged.

Extract any new or updated information from the user message below.
Return ONLY a JSON object with the fields that were mentioned or updated.
Use null for fields not mentioned. Never invent values.

Field types:
- name: string
- date: "YYYY-MM-DD"
- time: "HH:MM"
- description: string
- seat_types: object like {{"VIP": 10000, "Regular": 5000}}
- purchase_start: "YYYY-MM-DD"
- purchase_end: "YYYY-MM-DD"
- ticket_limit: integer
- venue_name: string
- venue_address: string
- capacity: integer
- organizer_name: string
- organizer_email: string
- category: string
- language: string
- is_recurring: boolean
- recurrence_frequency: string or null
- is_online: boolean

Respond with a pure JSON object, no markdown fences, no explanation.
"""


class SessionState:

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.draft: Dict[str, Any] = {}
        self.awaiting_confirmation = False
        self.completed = False
        self.last_asked_field: Optional[str] = None
        self.history: List[Dict[str, str]] = []  # [{role, content}]


class ChatbotService:

    def __init__(self) -> None:
        self._llm = None  # lazy-initialised on first use
        self._sessions: Dict[str, SessionState] = {}

    def _get_llm(self) -> ChatOpenAI:
        if self._llm is None:
            s = get_settings()
            self._llm = ChatOpenAI(
                model=s.gemini_model,
                api_key=s.gemini_api_key,
                base_url=s.gemini_base_url,
                temperature=0.2,
            )
            logger.info("LLM model=%s (Gemini)", s.gemini_model)
        return self._llm

    # ── public interface ──────────────────────────────────────────────────────

    def get_or_create_session(self, session_id: str) -> SessionState:
        if session_id not in self._sessions:
            self._sessions[session_id] = SessionState(session_id)
        return self._sessions[session_id]

    def reset_session(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)
        vector_store.clear_session(session_id)

    async def handle_message(self, session_id: str, user_message: str) -> ChatResponse:
        session = self.get_or_create_session(session_id)

        # Persist to vector store for semantic recall
        vector_store.add_message(session_id, "user", user_message)
        session.history.append({"role": "user", "content": user_message})

        if session.completed:
            return ChatResponse(
                scenario="success_save",
                message="This event has already been saved. Start a new session to create another event.",
            )

        # handle confirmation turn
        if session.awaiting_confirmation:
            return await self._handle_confirmation(session, user_message)

        updates = self._try_bare_assignment(session.last_asked_field, user_message)
        if updates is None:
            updates = await self._extract_fields(
                session.draft, user_message, asked_field=session.last_asked_field
            )
        if updates:
            updates = {k: v for k, v in updates.items() if k in FIELD_PROMPTS}
            session.draft.update({k: v for k, v in updates.items() if v is not None})

        # validate just-updated fields immediately
        updated_keys = {k for k, v in (updates or {}).items() if v is not None}
        per_field_error, bad_keys = self._validate_updates(session.draft, updated_keys)
        if per_field_error:
            for k in bad_keys:
                session.draft.pop(k, None)
            response = ChatResponse(
                scenario="invalid_input",
                message=f"{per_field_error} Could you correct it?",
            )
            self._append_assistant(session, response.message)
            return response

        # full draft validation (cross-field rules, runs only when complete)
        validation_error = self._validate_draft(session.draft)
        if validation_error and self._draft_looks_complete(session.draft):
            response = ChatResponse(
                scenario="invalid_input",
                message=f"There's an issue with the data: {validation_error}. Could you correct it?",
            )
            self._append_assistant(session, response.message)
            return response

        # check completeness
        missing = self._missing_fields(session.draft)
        if not missing:
            return self._ask_for_confirmation(session)

        # ask for next missing field
        next_field = missing[0]
        prompt = FIELD_PROMPTS.get(next_field, f"Could you provide the {next_field}?")
        session.last_asked_field = next_field

        if updates:
            ack = self._build_ack(updates)
            message = f"{ack} {prompt}"
        else:
            message = prompt

        response = ChatResponse(scenario="missing_field", message=message)
        self._append_assistant(session, response.message)
        return response

    def _ask_for_confirmation(self, session: SessionState) -> ChatResponse:
        draft = session.draft
        summary = self._build_summary(draft)
        session.awaiting_confirmation = True
        message = (
            f"Great, I have all the details! Here's a summary:\n\n{summary}\n\n"
            "Shall I save this event? (Yes / No)"
        )
        response = ChatResponse(scenario="confirmation", message=message)
        self._append_assistant(session, response.message)
        return response

    async def _handle_confirmation(self, session: SessionState, user_message: str) -> ChatResponse:
        affirmative = re.search(
            r"\b(yes|yeah|yep|sure|ok|okay|go ahead|save|confirm|do it)\b",
            user_message.lower(),
        )
        if affirmative:
            session.awaiting_confirmation = False
            session.completed = True
            response = ChatResponse(
                scenario="success_save",
                message="Saving your event now…",
            )
        else:
            session.awaiting_confirmation = False
            updates = await self._extract_fields(session.draft, user_message, asked_field=None)
            if updates:
                updates = {k: v for k, v in updates.items() if k in FIELD_PROMPTS}
                session.draft.update({k: v for k, v in updates.items() if v is not None})
                ack = self._build_ack(updates)
                response = ChatResponse(
                    scenario="update_previous_field",
                    message=f"{ack} Anything else to update, or shall I re-summarize?",
                )
            else:
                response = ChatResponse(
                    scenario="missing_field",
                    message="No problem! What would you like to change?",
                )
        self._append_assistant(session, response.message)
        return response

    # LangChain extraction

    async def _extract_fields(
        self,
        draft: Dict[str, Any],
        user_message: str,
        asked_field: Optional[str] = None,
    ) -> Dict[str, Any]:
        system_content = EXTRACTION_SYSTEM_PROMPT.format(
            draft=json.dumps(draft, default=str, indent=2),
            asked_field=asked_field or "(no specific field — extract whatever is mentioned)",
        )
        messages = [
            SystemMessage(content=system_content),
            HumanMessage(content=user_message),
        ]
        try:
            response = await self._get_llm().ainvoke(messages)
            raw = response.content.strip()
            # Strip markdown fences if any
            raw = re.sub(r"^```[a-z]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)
            return json.loads(raw)
        except (json.JSONDecodeError, Exception) as exc:
            logger.warning("Extraction failed: %s", exc)
            return {}

    @staticmethod
    def _missing_fields(draft: Dict[str, Any]) -> List[str]:
        return [f for f in REQUIRED_FIELDS if not draft.get(f)]

    @staticmethod
    def _draft_looks_complete(draft: Dict[str, Any]) -> bool:
        return not any(draft.get(f) is None for f in REQUIRED_FIELDS)

    _MULTI_WORDS = frozenset(
        {"also", "actually", "and", "but", "change", "update", "instead", "should"}
    )

    @staticmethod
    def _try_bare_assignment(asked_field: Optional[str], message: str) -> Optional[Dict[str, Any]]:
        if not asked_field:
            return None
        msg = message.strip()
        if not msg or len(msg) > 100:
            return None
        if "," in msg:
            return None
        words = set(re.findall(r"\b[a-z]+\b", msg.lower()))
        if words & ChatbotService._MULTI_WORDS:
            return None

        if asked_field in {"ticket_limit", "capacity"}:
            try:
                return {asked_field: int(msg)}
            except ValueError:
                return None

        if asked_field in {"date", "purchase_start", "purchase_end"}:
            if re.match(r"^\d{4}-\d{2}-\d{2}$", msg):
                return {asked_field: msg}
            return None

        if asked_field == "time":
            if re.match(r"^\d{1,2}:\d{2}(:\d{2})?$", msg):
                return {asked_field: msg}
            return None

        if asked_field in {
            "name",
            "venue_name",
            "venue_address",
            "organizer_name",
            "organizer_email",
            "category",
        }:
            # Free text: keep if not obviously a sentence
            if len(msg) > 60 and " " in msg:
                return None
            return {asked_field: msg}

        return None

    @staticmethod
    def _validate_draft(draft: Dict[str, Any]) -> Optional[str]:
        if ChatbotService._missing_fields(draft):
            return None  # Don't validate incomplete drafts
        try:
            EventCreate(**draft)
            return None
        except ValidationError as exc:
            first_error = exc.errors()[0]
            return first_error.get("msg", str(exc))

    @staticmethod
    def _validate_updates(draft: Dict[str, Any], updated_keys: set) -> Tuple[Optional[str], set]:
        if not updated_keys:
            return None, set()
        try:
            EventCreate(**draft)
            return None, set()
        except ValidationError as exc:
            msgs: List[str] = []
            bad: set = set()
            for err in exc.errors():
                if not err.get("loc"):
                    continue
                field = err["loc"][0]
                if field in updated_keys and err.get("type") != "missing":
                    msgs.append(err.get("msg", "invalid value"))
                    bad.add(field)
            return ("; ".join(msgs) if msgs else None), bad

    @staticmethod
    def _build_ack(updates: Dict[str, Any]) -> str:
        parts = []
        for field, value in updates.items():
            if value is not None:
                readable = field.replace("_", " ").title()
                parts.append(f"{readable}: {value}")
        if not parts:
            return "Got it."
        return "Got it — updated " + ", ".join(parts) + "."

    @staticmethod
    def _build_summary(draft: Dict[str, Any]) -> str:
        lines = []
        label_map = {
            "name": "Event Name",
            "date": "Date",
            "time": "Time",
            "description": "Description",
            "seat_types": "Seat Types & Prices",
            "ticket_limit": "Ticket Limit / Person",
            "purchase_start": "Sale Opens",
            "purchase_end": "Sale Closes",
            "venue_name": "Venue",
            "venue_address": "Venue Address",
            "capacity": "Capacity",
            "organizer_name": "Organizer",
            "organizer_email": "Organizer Email",
            "category": "Category",
            "language": "Language",
            "is_recurring": "Recurring",
            "is_online": "Online",
        }
        for key, label in label_map.items():
            val = draft.get(key)
            if val is not None:
                lines.append(f"• {label}: {val}")
        return "\n".join(lines)

    @staticmethod
    def _append_assistant(session: SessionState, content: str) -> None:
        vector_store.add_message(session.session_id, "assistant", content)
        session.history.append({"role": "assistant", "content": content})

    def get_draft(self, session_id: str) -> Optional[Dict[str, Any]]:
        session = self._sessions.get(session_id)
        return deepcopy(session.draft) if session else None

    def is_completed(self, session_id: str) -> bool:
        session = self._sessions.get(session_id)
        return session.completed if session else False


chatbot_service = ChatbotService()
