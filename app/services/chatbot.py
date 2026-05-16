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
from app.services.database import db_service
from app.services.vector_store import vector_store

logger = logging.getLogger(__name__)

_RECALL_PATTERNS = (
    r"\bremind\b.*\b(event|previous|last)\b",
    r"\b(show|list|view|tell me about)\b.*\b(events?|previous|past|recent)\b",
    r"\b(my|the)\s+(last|previous|recent)\s+events?\b",
    r"\bwhat\s+events?\s+(have I|did I|are there)",
)


# Required fields
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

        # semantic recall: "remind me of the last event I created" / "show my events"
        if not session.draft and self._looks_like_recall(user_message):
            recall = await self._handle_recall(session_id, user_message)
            if recall is not None:
                return self._respond(session, recall.scenario, recall.message)

        updates = self._try_bare_assignment(session.last_asked_field, user_message)
        if updates is None:
            updates = await self._extract_fields(
                session.draft,
                user_message,
                asked_field=session.last_asked_field,
                session_id=session_id,
            )
        updates = self._apply_updates(session, updates)

        # validate just-updated fields immediately
        per_field_error, bad_keys = self._validate_updates(session.draft, set(updates))
        if per_field_error:
            for k in bad_keys:
                session.draft.pop(k, None)
            return self._respond(
                session, "invalid_input", f"{per_field_error} Could you correct it?"
            )

        # full draft validation (cross-field rules, runs only when complete)
        validation_error = self._validate_draft(session.draft)
        if validation_error and self._draft_looks_complete(session.draft):
            return self._respond(
                session,
                "invalid_input",
                f"There's an issue with the data: {validation_error}. Could you correct it?",
            )

        # check completeness
        missing = self._missing_fields(session.draft)
        if not missing:
            return self._ask_for_confirmation(session)

        # ask for next missing field
        next_field = missing[0]
        prompt = FIELD_PROMPTS.get(next_field, f"Could you provide the {next_field}?")
        session.last_asked_field = next_field

        message = f"{self._build_ack(updates)} {prompt}" if updates else prompt
        return self._respond(session, "missing_field", message)

    def _ask_for_confirmation(self, session: SessionState) -> ChatResponse:
        summary = self._build_summary(session.draft)
        session.awaiting_confirmation = True
        message = (
            f"Great, I have all the details! Here's a summary:\n\n{summary}\n\n"
            "Shall I save this event? (Yes / No)"
        )
        return self._respond(session, "confirmation", message)

    async def _handle_confirmation(self, session: SessionState, user_message: str) -> ChatResponse:
        session.awaiting_confirmation = False
        affirmative = re.search(
            r"\b(yes|yeah|yep|sure|ok|okay|go ahead|save|confirm|do it)\b",
            user_message.lower(),
        )
        if affirmative:
            session.completed = True
            return self._respond(session, "success_save", "Saving your event now…")

        updates = await self._extract_fields(session.draft, user_message, asked_field=None)
        updates = self._apply_updates(session, updates)
        if updates:
            return self._respond(
                session,
                "update_previous_field",
                f"{self._build_ack(updates)} Anything else to update, or shall I re-summarize?",
            )
        return self._respond(session, "missing_field", "No problem! What would you like to change?")

    async def _extract_fields(
        self,
        draft: Dict[str, Any],
        user_message: str,
        asked_field: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        system_content = EXTRACTION_SYSTEM_PROMPT.format(
            draft=json.dumps(draft, default=str, indent=2),
            asked_field=asked_field or "(no specific field — extract whatever is mentioned)",
        )
        if session_id:
            try:
                recalled = vector_store.semantic_search(
                    user_message, session_id=session_id, n_results=3
                )
                recalled = [r for r in recalled if r and r != user_message]
                if recalled:
                    system_content += (
                        "\n\nRelevant prior turns from this session "
                        "(do not echo back; use only for context):\n"
                        + "\n".join(f"- {r}" for r in recalled)
                    )
            except Exception as exc:
                logger.debug("semantic_search failed: %s", exc)

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
    def _looks_like_recall(message: str) -> bool:
        text = message.lower()
        return any(re.search(p, text) for p in _RECALL_PATTERNS)

    async def _handle_recall(self, session_id: str, user_message: str) -> Optional[ChatResponse]:
        try:
            events = await db_service.get_events(limit=5)
        except Exception as exc:
            logger.warning("recall: get_events failed: %s", exc)
            return None

        if not events:
            related = vector_store.semantic_search(user_message, n_results=3)
            extra = ""
            if related:
                extra = "\n\nFrom past conversations:\n" + "\n".join(f"- {r}" for r in related)
            return ChatResponse(
                scenario="missing_field",
                message=(
                    "You haven't saved any events yet."
                    + extra
                    + "\n\nWhat's the name of the event you want to create?"
                ),
            )

        lines = [
            f"• {e['name']} — {e['date']} at {e['venue_name']} ({e['category']})" for e in events
        ]
        return ChatResponse(
            scenario="missing_field",
            message=(
                "Here are your most recent events:\n\n"
                + "\n".join(lines)
                + "\n\nWould you like to create a new event? If so, what's its name?"
            ),
        )

    @staticmethod
    def _apply_updates(
        session: SessionState, raw_updates: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        if not raw_updates:
            return {}
        clean = {k: v for k, v in raw_updates.items() if k in FIELD_PROMPTS and v is not None}
        session.draft.update(clean)
        return clean

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
            if len(msg) > 60 and " " in msg:
                return None
            return {asked_field: msg}

        return None

    @staticmethod
    def _validate_draft(draft: Dict[str, Any]) -> Optional[str]:
        if ChatbotService._missing_fields(draft):
            return None
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

    @classmethod
    def _respond(cls, session: SessionState, scenario: str, message: str) -> ChatResponse:
        cls._append_assistant(session, message)
        return ChatResponse(scenario=scenario, message=message)

    def get_draft(self, session_id: str) -> Optional[Dict[str, Any]]:
        session = self._sessions.get(session_id)
        return deepcopy(session.draft) if session else None

    def is_completed(self, session_id: str) -> bool:
        session = self._sessions.get(session_id)
        return session.completed if session else False


chatbot_service = ChatbotService()
