from __future__ import annotations

from datetime import date, datetime, time
from typing import List, Optional

from pydantic import BaseModel, Field, constr, root_validator, validator

from .days import normalize_day_list

class DateRangeQuery(BaseModel):
    from_date: date = Field(alias="from")
    days: int = 30

    @validator("days")
    def validate_days(cls, value: int) -> int:
        if value < 1:
            raise ValueError("days must be at least 1")
        if value > 90:
            raise ValueError("days must be <= 90")
        return value


class DateAvailabilityResponseItem(BaseModel):
    date: date
    has_availability: bool
    total_slots: int


class DateAvailabilityResponse(BaseModel):
    from_date: date = Field(alias="from")
    to_date: date = Field(alias="to")
    dates: List[DateAvailabilityResponseItem]


class SlotExposureQuery(BaseModel):
    date: date
    location_id: constr(strip_whitespace=True)
    person_id: Optional[str] = None
    timezone: Optional[str] = None


class SlotExposureItem(BaseModel):
    slot_id: str
    start_at: datetime
    end_at: datetime
    remaining: int


class SlotExposureResponse(BaseModel):
    date: date
    person_id: Optional[str] = None
    total_available: int
    has_more: bool
    exposed_slots: List[SlotExposureItem]


class CustomerInfo(BaseModel):
    name: str
    phone: str
    email: Optional[str]


class BookingRequest(BaseModel):
    slot_id: str
    customer: CustomerInfo
    notes: Optional[str]
    consent: Optional[dict]
    source: Optional[str]


class BookingResponse(BaseModel):
    booking_id: str
    status: str
    slot_id: str
    hold_expires_at: Optional[datetime]


class BookingConfirmResponse(BaseModel):
    booking_id: str
    status: str
    slot_id: str


class AvailabilityRulePayload(BaseModel):
    location_id: str
    person_id: Optional[str]
    rule_kind: constr(strip_whitespace=True)
    days_of_week: Optional[List[str]]
    start_time: time
    end_time: time
    slot_capacity: int = 1
    slot_granularity_minutes: int = 15
    slot_duration_minutes: int = 30
    valid_from: Optional[date]
    valid_to: Optional[date]
    is_closed: bool = False

    @root_validator
    def validate_rule(cls, values):
        start_time: time = values.get("start_time")
        end_time: time = values.get("end_time")
        if start_time >= end_time:
            raise ValueError("start_time must be before end_time")
        capacity = values.get("slot_capacity")
        if capacity < 0:
            raise ValueError("slot_capacity must be >= 0")
        granularity = values.get("slot_granularity_minutes")
        if granularity <= 0:
            raise ValueError("slot_granularity_minutes must be > 0")
        duration = values.get("slot_duration_minutes")
        if duration is None or duration <= 0:
            raise ValueError("slot_duration_minutes must be > 0")
        return values

    @validator("days_of_week")
    def validate_days_of_week(cls, value: Optional[List[str]]):
        if value is None:
            return value
        return normalize_day_list(value)


class AvailabilityRuleResponse(BaseModel):
    rule_id: str


class SlotGenerationRequest(BaseModel):
    location_id: str
    from_date: date = Field(alias="from")
    to_date: date = Field(alias="to")
    dry_run: bool = False

    @validator("to_date")
    def validate_to_date(cls, value: date, values):
        start = values.get("from_date")
        if start and value < start:
            raise ValueError("to must be greater than or equal to from")
        return value


class SlotGenerationResponse(BaseModel):
    created: int
    skipped: int


class BookingListItem(BaseModel):
    booking_id: str
    status: str
    date: date
    start_at: datetime
    customer: CustomerInfo
    person_id: Optional[str]


class BookingListResponse(BaseModel):
    page: int
    page_size: int
    total: int
    items: List[BookingListItem]


class BookingPatchRequest(BaseModel):
    action: constr(strip_whitespace=True)
    reason: Optional[str]
    new_slot_id: Optional[str]

    @root_validator
    def validate_payload(cls, values):
        action = values.get("action")
        if action not in {"cancel", "reschedule"}:
            raise ValueError("action must be cancel or reschedule")
        if action == "cancel" and not values.get("reason"):
            raise ValueError("reason is required when cancelling")
        if action == "reschedule" and not values.get("new_slot_id"):
            raise ValueError("new_slot_id is required when rescheduling")
        return values


class BookingPatchResponse(BaseModel):
    booking_id: str
    status: str
    slot_id: str
