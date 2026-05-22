from __future__ import annotations

from datetime import date, time

import pytest
from pydantic import ValidationError

from app.models.event import EventCreate


def valid_payload(**overrides) -> dict:
    base = {
        "name": "Kyoto Jazz Night",
        "date": date(2027, 6, 1),
        "time": time(19, 0),
        "description": "A live jazz performance in Kyoto.",
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
    base.update(overrides)
    return base


class TestValidEvent:
    def test_full_valid_event(self):
        event = EventCreate(**valid_payload())
        assert event.name == "Kyoto Jazz Night"
        assert event.organizer_email == "info@fenix.co.jp"

    def test_email_normalised_to_lowercase(self):
        event = EventCreate(**valid_payload(organizer_email="INFO@FENIX.CO.JP"))
        assert event.organizer_email == "info@fenix.co.jp"

    def test_optional_description_none(self):
        payload = valid_payload()
        payload.pop("description")
        event = EventCreate(**payload)
        assert event.description is None


class TestEmailValidation:
    @pytest.mark.parametrize(
        "bad_email",
        [
            "not-an-email",
            "missing@",
            "@nodomain.com",
            "spaces in@email.com",
            "double@@domain.com",
        ],
    )
    def test_invalid_emails_rejected(self, bad_email):
        with pytest.raises(ValidationError) as exc_info:
            EventCreate(**valid_payload(organizer_email=bad_email))
        errors = exc_info.value.errors()
        assert any("email" in str(e).lower() or "valid" in str(e).lower() for e in errors)

    @pytest.mark.parametrize(
        "good_email",
        [
            "user@example.com",
            "first.last+tag@subdomain.org",
            "info@fenix.co.jp",
        ],
    )
    def test_valid_emails_accepted(self, good_email):
        event = EventCreate(**valid_payload(organizer_email=good_email))
        assert event.organizer_email == good_email.lower()


class TestDateValidation:
    def test_invalid_date_string_rejected(self):
        with pytest.raises(ValidationError):
            EventCreate(**valid_payload(date="not-a-date"))

    def test_past_date_rejected(self):
        with pytest.raises(ValidationError) as exc_info:
            EventCreate(**valid_payload(
                date=date(2020, 1, 1),
                purchase_start=date(2019, 12, 1),
                purchase_end=date(2019, 12, 31),
            ))
        assert "future" in str(exc_info.value).lower()

    def test_today_date_accepted(self):
        import datetime as _dt
        today = _dt.date.today()
        event = EventCreate(**valid_payload(
            date=today,
            purchase_start=today,
            purchase_end=today,
        ))
        assert event.date == today

    def test_purchase_end_after_event_date_rejected(self):
        with pytest.raises(ValidationError) as exc_info:
            EventCreate(
                **valid_payload(
                    date=date(2027, 6, 1),
                    purchase_end=date(2027, 6, 15),  # after event date
                )
            )
        assert "purchase end" in str(exc_info.value).lower()

    def test_purchase_end_before_start_rejected(self):
        with pytest.raises(ValidationError) as exc_info:
            EventCreate(
                **valid_payload(
                    purchase_start=date(2027, 5, 1),
                    purchase_end=date(2027, 4, 1),  # before start
                )
            )
        assert "purchase end" in str(exc_info.value).lower()

    def test_valid_purchase_period_accepted(self):
        event = EventCreate(
            **valid_payload(
                purchase_start=date(2027, 3, 1),
                purchase_end=date(2027, 5, 31),
                date=date(2027, 6, 1),
            )
        )
        assert event.purchase_start < event.purchase_end


class TestTicketValidation:
    def test_zero_ticket_limit_rejected(self):
        with pytest.raises(ValidationError):
            EventCreate(**valid_payload(ticket_limit=0))

    def test_negative_ticket_limit_rejected(self):
        with pytest.raises(ValidationError):
            EventCreate(**valid_payload(ticket_limit=-3))

    def test_zero_capacity_rejected(self):
        with pytest.raises(ValidationError):
            EventCreate(**valid_payload(capacity=0))

    def test_negative_price_in_seat_types_rejected(self):
        with pytest.raises(ValidationError):
            EventCreate(**valid_payload(seat_types={"VIP": -100}))

    def test_empty_seat_types_rejected(self):
        with pytest.raises(ValidationError):
            EventCreate(**valid_payload(seat_types={}))


class TestRecurringValidation:
    def test_recurring_without_frequency_rejected(self):
        with pytest.raises(ValidationError) as exc_info:
            EventCreate(**valid_payload(is_recurring=True, recurrence_frequency=None))
        assert "recurrence" in str(exc_info.value).lower()

    def test_recurring_with_frequency_accepted(self):
        event = EventCreate(
            **valid_payload(
                is_recurring=True,
                recurrence_frequency="monthly",
            )
        )
        assert event.is_recurring is True
        assert event.recurrence_frequency == "monthly"

    def test_non_recurring_no_frequency_accepted(self):
        event = EventCreate(**valid_payload(is_recurring=False))
        assert event.is_recurring is False


class TestNameValidation:
    def test_empty_name_rejected(self):
        with pytest.raises(ValidationError):
            EventCreate(**valid_payload(name="   "))

    def test_whitespace_stripped_from_name(self):
        event = EventCreate(**valid_payload(name="  Kyoto Jazz Night  "))
        assert event.name == "Kyoto Jazz Night"
