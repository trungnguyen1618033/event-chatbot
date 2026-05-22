from __future__ import annotations

import re
from datetime import date, time
from typing import Dict, Optional

from pydantic import BaseModel, field_validator, model_validator

RESERVED_TLDS = frozenset({"invalid", "test", "example", "localhost"})


class EventCreate(BaseModel):

    name: str
    date: date
    time: time
    description: Optional[str] = None
    seat_types: Dict[str, int]  # {"VIP": 10000, "Regular": 5000}
    purchase_start: date
    purchase_end: date
    ticket_limit: int
    venue_name: str
    venue_address: str
    capacity: int
    organizer_name: str
    organizer_email: str
    category: str
    language: str = "English"
    is_recurring: bool = False
    recurrence_frequency: Optional[str] = None
    is_online: bool = False

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Event name must not be empty.")
        return v

    @field_validator("date")
    @classmethod
    def date_not_in_past(cls, v: date) -> date:
        if v < date.today():
            raise ValueError("Event date must be today or in the future.")
        return v

    @field_validator("organizer_email")
    @classmethod
    def valid_email(cls, v: str) -> str:
        v = v.strip()
        pattern = r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$"
        if not re.match(pattern, v):
            raise ValueError(f"'{v}' is not a valid email address.")
        tld = v.rsplit(".", 1)[-1].lower()
        if tld in RESERVED_TLDS:
            raise ValueError(f"'{v}' is not a valid email address.")
        return v.lower()

    @field_validator("ticket_limit")
    @classmethod
    def positive_ticket_limit(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("Ticket limit must be a positive integer.")
        return v

    @field_validator("capacity")
    @classmethod
    def positive_capacity(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("Capacity must be a positive integer.")
        return v

    @field_validator("seat_types")
    @classmethod
    def seat_types_non_empty(cls, v: Dict[str, int]) -> Dict[str, int]:
        if not v:
            raise ValueError("At least one seat type with a price is required.")
        for seat, price in v.items():
            if price < 0:
                raise ValueError(f"Price for '{seat}' must be non-negative.")
        return v

    @model_validator(mode="after")
    def purchase_period_valid(self) -> "EventCreate":
        if self.purchase_end < self.purchase_start:
            raise ValueError("Purchase end date must be on or after the purchase start date.")
        if self.purchase_end > self.date:
            raise ValueError("Purchase end date must not be later than the event date.")
        return self

    @model_validator(mode="after")
    def recurrence_consistency(self) -> "EventCreate":
        if self.is_recurring and not self.recurrence_frequency:
            raise ValueError("Recurrence frequency is required when the event is recurring.")
        return self


class EventResponse(BaseModel):

    id: int
    name: str
    date: date
    time: time
    venue_name: str
    organizer_email: str


class ChatMessage(BaseModel):

    session_id: str
    message: str


class ChatResponse(BaseModel):

    role: str = "assistant"
    scenario: str
    message: str


class RegisterEventRequest(BaseModel):

    session_id: str
    event: EventCreate
