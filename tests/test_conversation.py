"""
tests/test_conversation.py
Tests for chatbot session state, missing-field detection,
and scenario tagging — without requiring a live OpenAI API key.
"""

from __future__ import annotations

from datetime import date, time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.event import ChatResponse
from app.services.chatbot import (
    REQUIRED_FIELDS,
    ChatbotService,
    SessionState,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def service():
    """ChatbotService with mocked LLM and vector store."""
    svc = ChatbotService.__new__(ChatbotService)
    svc._sessions = {}
    svc._llm = AsyncMock()
    return svc


FULL_DRAFT = {
    "name": "Kyoto Jazz Night",
    "date": date(2026, 3, 10),
    "time": time(19, 0),
    "description": "A live jazz performance.",
    "seat_types": {"VIP": 10000, "Regular": 5000},
    "purchase_start": date(2026, 1, 1),
    "purchase_end": date(2026, 3, 9),
    "ticket_limit": 4,
    "venue_name": "Kyoto Concert Hall",
    "venue_address": "123 Sakyo-ku, Kyoto",
    "capacity": 1000,
    "organizer_name": "Fenix Entertainment",
    "organizer_email": "info@fenix.co.jp",
    "category": "Concert",
    "language": "Japanese",
    "is_recurring": False,
    "is_online": False,
}


# ── SessionState ──────────────────────────────────────────────────────────────


class TestSessionState:
    def test_new_session_empty_draft(self):
        session = SessionState("s1")
        assert session.draft == {}
        assert not session.awaiting_confirmation
        assert not session.completed

    def test_get_or_create_returns_same_instance(self, service):
        s1 = service.get_or_create_session("abc")
        s2 = service.get_or_create_session("abc")
        assert s1 is s2

    def test_different_sessions_are_isolated(self, service):
        s1 = service.get_or_create_session("s1")
        s2 = service.get_or_create_session("s2")
        s1.draft["name"] = "Event A"
        assert "name" not in s2.draft


# ── Missing field detection ────────────────────────────────────────────────────


class TestMissingFields:
    def test_all_required_missing_on_empty_draft(self, service):
        missing = service._missing_fields({})
        assert set(missing) == set(REQUIRED_FIELDS)

    def test_filled_fields_not_in_missing(self, service):
        draft = {"name": "Test Event"}
        missing = service._missing_fields(draft)
        assert "name" not in missing

    def test_no_missing_when_all_required_present(self, service):
        missing = service._missing_fields(FULL_DRAFT)
        assert missing == []


# ── Validation helper ─────────────────────────────────────────────────────────


class TestValidateDraft:
    def test_valid_full_draft_returns_none(self, service):
        err = service._validate_draft(FULL_DRAFT)
        assert err is None

    def test_incomplete_draft_returns_none_not_error(self, service):
        """We don't validate until draft is complete."""
        err = service._validate_draft({"name": "Test"})
        assert err is None

    def test_bad_email_in_complete_draft_returns_error(self, service):
        bad_draft = {**FULL_DRAFT, "organizer_email": "not-an-email"}
        err = service._validate_draft(bad_draft)
        assert err is not None
        assert "email" in err.lower() or "valid" in err.lower()

    def test_negative_ticket_limit_in_complete_draft_returns_error(self, service):
        bad_draft = {**FULL_DRAFT, "ticket_limit": -1}
        err = service._validate_draft(bad_draft)
        assert err is not None


# ── handle_message — mocked LLM ───────────────────────────────────────────────


class TestHandleMessage:
    @pytest.mark.asyncio
    async def test_first_message_returns_missing_field_scenario(self, service):
        """On first message, chatbot should ask for missing fields."""
        with (
            patch.object(service, "_extract_fields", new=AsyncMock(return_value={})),
            patch("app.services.chatbot.vector_store") as mock_vs,
        ):
            mock_vs.add_message = MagicMock()
            response = await service.handle_message("sess1", "I want to create an event")
        assert response.scenario == "missing_field"
        assert isinstance(response.message, str) and len(response.message) > 0

    @pytest.mark.asyncio
    async def test_extracted_name_reduces_missing_count(self, service):
        with (
            patch.object(
                service, "_extract_fields", new=AsyncMock(return_value={"name": "Summer Fest"})
            ),
            patch("app.services.chatbot.vector_store") as mock_vs,
        ):
            mock_vs.add_message = MagicMock()
            await service.handle_message("sess2", "My event is Summer Fest")
        session = service.get_or_create_session("sess2")
        assert session.draft.get("name") == "Summer Fest"

    @pytest.mark.asyncio
    async def test_full_draft_triggers_confirmation(self, service):
        session = service.get_or_create_session("sess3")
        session.draft = {**FULL_DRAFT}

        with (
            patch.object(service, "_extract_fields", new=AsyncMock(return_value={})),
            patch("app.services.chatbot.vector_store") as mock_vs,
        ):
            mock_vs.add_message = MagicMock()
            response = await service.handle_message("sess3", "That's all.")
        assert response.scenario == "confirmation"
        assert "save" in response.message.lower() or "confirm" in response.message.lower()

    @pytest.mark.asyncio
    async def test_yes_after_confirmation_marks_completed(self, service):
        session = service.get_or_create_session("sess4")
        session.draft = {**FULL_DRAFT}
        session.awaiting_confirmation = True

        with patch("app.services.chatbot.vector_store") as mock_vs:
            mock_vs.add_message = MagicMock()
            response = await service.handle_message("sess4", "Yes, save it")

        assert response.scenario == "success_save"
        assert service.is_completed("sess4")

    @pytest.mark.asyncio
    async def test_no_after_confirmation_allows_updates(self, service):
        session = service.get_or_create_session("sess5")
        session.draft = {**FULL_DRAFT}
        session.awaiting_confirmation = True

        with (
            patch.object(
                service, "_extract_fields", new=AsyncMock(return_value={"date": "2026-03-12"})
            ),
            patch("app.services.chatbot.vector_store") as mock_vs,
        ):
            mock_vs.add_message = MagicMock()
            response = await service.handle_message("sess5", "No, change the date to March 12th")

        assert response.scenario == "update_previous_field"
        assert not service.is_completed("sess5")

    @pytest.mark.asyncio
    async def test_completed_session_returns_success_scenario(self, service):
        session = service.get_or_create_session("sess6")
        session.completed = True

        with patch("app.services.chatbot.vector_store") as mock_vs:
            mock_vs.add_message = MagicMock()
            response = await service.handle_message("sess6", "Hello again")

        assert response.scenario == "success_save"


# ── Summary / acknowledgement helpers ────────────────────────────────────────


class TestHelpers:
    def test_build_ack_with_updates(self, service):
        updates = {"name": "Test Event", "date": "2026-01-01"}
        ack = service._build_ack(updates)
        assert "name" in ack.lower() or "Test Event" in ack

    def test_build_ack_empty_returns_got_it(self, service):
        ack = service._build_ack({})
        assert "Got it" in ack

    def test_build_summary_includes_all_keys(self, service):
        summary = service._build_summary(FULL_DRAFT)
        assert "Kyoto Jazz Night" in summary
        assert "Kyoto Concert Hall" in summary
        assert "info@fenix.co.jp" in summary
