from __future__ import annotations

from datetime import date, time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app, raise_server_exceptions=False)


VALID_EVENT = {
    "name": "Test Jazz Night",
    "date": "2026-03-10",
    "time": "19:00:00",
    "description": "A test event.",
    "seat_types": {"VIP": 10000, "Regular": 5000},
    "purchase_start": "2026-01-01",
    "purchase_end": "2026-03-09",
    "ticket_limit": 4,
    "venue_name": "Test Hall",
    "venue_address": "1-1 Test Street, Tokyo",
    "capacity": 500,
    "organizer_name": "Test Org",
    "organizer_email": "test@org.com",
    "category": "Concert",
    "language": "Japanese",
    "is_recurring": False,
    "is_online": False,
}


def test_health_endpoint():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


class TestRegisterEvent:
    def _mock_db(self, exists=False, insert_result=None):
        default_result = {
            "id": 1,
            "name": "Test Jazz Night",
            "date": date(2026, 3, 10),
            "time": time(19, 0),
            "venue_name": "Test Hall",
            "organizer_email": "test@org.com",
            "created_at": "2026-01-01T00:00:00",
        }
        return {
            "event_exists": AsyncMock(return_value=exists),
            "insert_event": AsyncMock(return_value=insert_result or default_result),
        }

    @patch("app.api.routes.db_service")
    def test_valid_payload_returns_201(self, mock_db):
        mock_db.event_exists = AsyncMock(return_value=False)
        mock_db.insert_event = AsyncMock(
            return_value={
                "id": 1,
                "name": "Test Jazz Night",
                "date": date(2026, 3, 10),
                "time": time(19, 0),
                "venue_name": "Test Hall",
                "organizer_email": "test@org.com",
            }
        )

        resp = client.post(
            "/api/register-event",
            json={"session_id": "test-sess", "event": VALID_EVENT},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["status"] == "success"
        assert "Test Jazz Night" in body["message"]

    @patch("app.api.routes.db_service")
    def test_duplicate_event_returns_409(self, mock_db):
        mock_db.event_exists = AsyncMock(return_value=True)

        resp = client.post(
            "/api/register-event",
            json={"session_id": "test-sess", "event": VALID_EVENT},
        )
        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail["scenario"] == "error_db"

    def test_invalid_email_returns_422(self):
        bad = {**VALID_EVENT, "organizer_email": "not-an-email"}
        resp = client.post(
            "/api/register-event",
            json={"session_id": "test-sess", "event": bad},
        )
        assert resp.status_code == 422

    def test_missing_required_field_returns_422(self):
        incomplete = {k: v for k, v in VALID_EVENT.items() if k != "venue_name"}
        resp = client.post(
            "/api/register-event",
            json={"session_id": "test-sess", "event": incomplete},
        )
        assert resp.status_code == 422

    def test_negative_ticket_limit_returns_422(self):
        bad = {**VALID_EVENT, "ticket_limit": -1}
        resp = client.post(
            "/api/register-event",
            json={"session_id": "test-sess", "event": bad},
        )
        assert resp.status_code == 422

    def test_invalid_date_format_returns_422(self):
        bad = {**VALID_EVENT, "date": "March 10th"}
        resp = client.post(
            "/api/register-event",
            json={"session_id": "test-sess", "event": bad},
        )
        assert resp.status_code == 422

    @patch("app.api.routes.db_service")
    def test_db_error_returns_500(self, mock_db):
        mock_db.event_exists = AsyncMock(return_value=False)
        mock_db.insert_event = AsyncMock(side_effect=Exception("DB down"))

        resp = client.post(
            "/api/register-event",
            json={"session_id": "test-sess", "event": VALID_EVENT},
        )
        assert resp.status_code == 500
        detail = resp.json()["detail"]
        assert detail["scenario"] == "error_db"


# /api/chat
class TestChatEndpoint:
    @patch("app.api.routes.chatbot_service")
    def test_chat_returns_chat_response_shape(self, mock_svc):
        mock_svc.handle_message = AsyncMock(
            return_value=MagicMock(
                scenario="missing_field",
                message="What is the event name?",
                role="assistant",
                model_dump=lambda: {
                    "role": "assistant",
                    "scenario": "missing_field",
                    "message": "What is the event name?",
                },
            )
        )
        mock_svc.is_completed.return_value = False

        resp = client.post(
            "/api/chat",
            json={"session_id": "chat-sess", "message": "Hi"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "scenario" in body
        assert "message" in body
        assert body["role"] == "assistant"

    @patch("app.api.routes.chatbot_service")
    def test_chat_missing_field_requests_clarification(self, mock_svc):
        """Spec §6 API Tests — 'Missing field → chatbot requests clarification'.

        Verifies /api/chat surfaces the missing_field scenario with a
        clarifying next-question message when the user hasn't provided info.
        """
        from app.models.event import ChatResponse as CR

        mock_svc.handle_message = AsyncMock(
            return_value=CR(
                scenario="missing_field",
                message="What is the name of your event?",
            )
        )
        mock_svc.is_completed.return_value = False

        resp = client.post(
            "/api/chat",
            json={"session_id": "missing-sess", "message": "I want to create an event"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["scenario"] == "missing_field"
        assert body["role"] == "assistant"
        # Clarification = an actionable question
        assert "?" in body["message"]

    @patch("app.api.routes.chatbot_service")
    @patch("app.api.routes.db_service")
    def test_chat_auto_saves_on_completion(self, mock_db, mock_svc):
        from app.models.event import ChatResponse as CR

        mock_svc.handle_message = AsyncMock(
            return_value=CR(scenario="success_save", message="Saving your event now…")
        )
        mock_svc.is_completed.return_value = True
        mock_svc.get_draft.return_value = VALID_EVENT

        mock_db.event_exists = AsyncMock(return_value=False)
        mock_db.insert_event = AsyncMock(
            return_value={
                "id": 7,
                "name": "Test Jazz Night",
                "date": date(2026, 3, 10),
                "time": time(19, 0),
                "venue_name": "Test Hall",
                "organizer_email": "test@org.com",
            }
        )

        resp = client.post(
            "/api/chat",
            json={"session_id": "save-sess", "message": "Yes, save it"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["scenario"] == "success_save"


# /api/sessions/{id} DELETE
def test_reset_session():
    with patch("app.api.routes.chatbot_service") as mock_svc:
        mock_svc.reset_session = MagicMock()
        resp = client.delete("/api/sessions/test-session-123")
    assert resp.status_code == 200
    assert "reset" in resp.json()["message"].lower()
