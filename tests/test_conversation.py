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


@pytest.fixture
def service():
    svc = ChatbotService.__new__(ChatbotService)
    svc._sessions = {}
    svc._llm = AsyncMock()
    return svc


FULL_DRAFT = {
    "name": "Kyoto Jazz Night",
    "date": date(2027, 6, 1),
    "time": time(19, 0),
    "description": "A live jazz performance.",
    "seat_types": {"VIP": 10000, "Regular": 5000},
    "purchase_start": date(2027, 3, 1),
    "purchase_end": date(2027, 5, 31),
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


class TestHandleMessage:
    @pytest.mark.asyncio
    async def test_first_message_returns_missing_field_scenario(self, service):
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
    async def test_handle_message_returns_invalid_input_on_bad_email(self, service):
        """Spec §6 Conversation Logic — invalid input must trigger invalid_input scenario."""
        session = service.get_or_create_session("sess-invalid")
        # Pre-fill everything except organizer_email so the bad email completes the draft
        # and triggers per-field validation in handle_message.
        session.draft = {k: v for k, v in FULL_DRAFT.items() if k != "organizer_email"}
        session.last_asked_field = "organizer_email"

        with (
            patch.object(
                service,
                "_extract_fields",
                new=AsyncMock(return_value={"organizer_email": "not-an-email"}),
            ),
            patch("app.services.chatbot.vector_store") as mock_vs,
        ):
            mock_vs.add_message = MagicMock()
            response = await service.handle_message("sess-invalid", "not-an-email")

        assert response.scenario == "invalid_input"
        assert "email" in response.message.lower()
        # The bad value must be dropped so the bot will re-ask the field
        assert "organizer_email" not in session.draft

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


class TestOptionalFields:
    async def test_optional_fields_asked_after_required(self, service):
        session = service.get_or_create_session("opt-test")
        # Only required fields — optional fields intentionally absent
        session.draft = {k: v for k, v in FULL_DRAFT.items() if k in REQUIRED_FIELDS}

        with (
            patch.object(service, "_extract_fields", new=AsyncMock(return_value={})),
            patch("app.services.chatbot.vector_store") as mock_vs,
        ):
            mock_vs.add_message = MagicMock()
            mock_vs.semantic_search = MagicMock(return_value=[])
            response = await service.handle_message("opt-test", "hello")

        assert response.scenario == "missing_field"
        assert session.last_asked_field in ["description", "language", "is_online", "is_recurring"]

    async def test_skip_optional_field_uses_default(self, service):
        session = service.get_or_create_session("skip-test")
        session.draft = {**FULL_DRAFT}
        session.last_asked_field = "language"

        with (
            patch.object(service, "_extract_fields", new=AsyncMock(return_value={})),
            patch("app.services.chatbot.vector_store") as mock_vs,
        ):
            mock_vs.add_message = MagicMock()
            mock_vs.semantic_search = MagicMock(return_value=[])
            await service.handle_message("skip-test", "skip")

        assert "language" in session.draft
        assert session.draft["language"] == "English"

    async def test_confirmation_shown_after_all_optional_answered(self, service):
        session = service.get_or_create_session("all-opt-test")
        session.draft = {
            **FULL_DRAFT,
            "description": "A great event",
            "language": "Japanese",
            "is_online": False,
            "is_recurring": False,
        }

        with (
            patch.object(service, "_extract_fields", new=AsyncMock(return_value={})),
            patch("app.services.chatbot.vector_store") as mock_vs,
        ):
            mock_vs.add_message = MagicMock()
            mock_vs.semantic_search = MagicMock(return_value=[])
            response = await service.handle_message("all-opt-test", "ok")

        assert response.scenario == "confirmation"


class TestRecallIsolation:
    async def test_recall_does_not_leak_other_sessions(self, service):
        with (
            patch("app.services.chatbot.db_service") as mock_db,
            patch("app.services.chatbot.vector_store") as mock_vs,
        ):
            mock_db.get_events = AsyncMock(return_value=[])
            mock_vs.semantic_search = MagicMock(return_value=["other session data"])
            mock_vs.add_message = MagicMock()

            await service._handle_recall("my-session", "show my events")

            mock_vs.semantic_search.assert_called_once_with(
                "show my events", session_id="my-session", n_results=3
            )


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
