from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from typing import Optional

import yaml
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse, Response
from zoneinfo import ZoneInfo

from . import exposure
from .days import day_names_to_iso
from .schemas import (
    AvailabilityRulePayload,
    AvailabilityRuleResponse,
    BookingConfirmResponse,
    BookingListItem,
    BookingListResponse,
    BookingPatchRequest,
    BookingPatchResponse,
    BookingRequest,
    BookingResponse,
    DateAvailabilityResponse,
    DateAvailabilityResponseItem,
    SlotExposureItem,
    SlotExposureQuery,
    SlotExposureResponse,
    SlotGenerationRequest,
    SlotGenerationResponse,
)
from .store import (
    AvailabilityRule,
    HoldExpiredError,
    NotFoundError,
    SlotFullError,
    SlotInstance,
    Location,
    BookingWithSlot,
    store,
)

tags_metadata = [
    {
        "name": "User",
        "description": "Public endpoints that surface availability and manage the booking lifecycle.",
    },
    {
        "name": "Admin",
        "description": "Administrative endpoints for managing availability rules, slots, and bookings.",
    },
]


app = FastAPI(
    title="General Scheduler API",
    version="1.0.0",
    description="APIs for exposing availability to end users while allowing admins to manage people, rules, slots, and bookings.",
    openapi_tags=tags_metadata,
    docs_url="/docs",
    redoc_url="/redoc",
)


@app.on_event("startup")
async def startup_event() -> None:
    await store.initialize()


async def get_idempotency_key(idempotency_key: str = Header(..., alias="Idempotency-Key")) -> str:
    return idempotency_key


def _get_user_key(request: Request) -> str:
    user_id = request.headers.get("X-User-Id")
    if user_id:
        return user_id
    session_id = request.headers.get("X-Session-Id")
    if session_id:
        return session_id
    return "anonymous"


@app.get("/v1/dates", response_model=DateAvailabilityResponse, tags=["User"])
async def get_dates(
    from_date: date = Query(default=date.today(), alias="from"),
    days: int = Query(default=30, ge=1, le=90),
    location_id: Optional[str] = Query(default=None),
    person_id: Optional[str] = Query(default=None),
):
    await store.expire_holds()
    to_date = from_date + timedelta(days=days)
    slots = await store.list_slots(
        location_id=location_id,
        person_id=person_id,
        start_date=from_date,
        end_date=to_date,
    )
    results: list[DateAvailabilityResponseItem] = []
    for offset in range((to_date - from_date).days + 1):
        day = from_date + timedelta(days=offset)
        filtered = [
            slot
            for slot in slots
            if slot.date == day
        ]
        available = [s for s in filtered if _remaining_capacity(s) > 0 and s.status != "blocked"]
        results.append(
            DateAvailabilityResponseItem(
                date=day,
                has_availability=bool(available),
                total_slots=len(available),
            )
        )
    return DateAvailabilityResponse(
        **{"from": from_date, "to": to_date},
        dates=results,
    )


@app.get("/v1/slots", response_model=SlotExposureResponse, tags=["User"])
async def get_slots(
    request: Request,
    query: SlotExposureQuery = Depends(),
):
    await store.expire_holds()
    try:
        location = await store.get_location(query.location_id)
        if query.person_id is not None:
            await store.get_person(query.person_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    slots = await store.list_slots(
        location_id=query.location_id,
        person_id=query.person_id,
        for_date=query.date,
    )
    available_slots = [slot for slot in slots if _remaining_capacity(slot) > 0 and slot.status != "blocked"]
    total_available = len(available_slots)
    if total_available == 0:
        return SlotExposureResponse(
            date=query.date,
            person_id=query.person_id,
            total_available=0,
            has_more=False,
            exposed_slots=[],
        )

    timezone = query.timezone or location.timezone
    user_key = _get_user_key(request)
    exposed = exposure.select_exposed_slots(
        available_slots,
        location_timezone=timezone,
        user_key=user_key,
        date_key=str(query.date),
        person_key=query.person_id or "",
    )
    slot_items = [
        SlotExposureItem(
            slot_id=slot.id,
            start_at=_to_timezone(slot.start_at, timezone),
            end_at=_to_timezone(slot.end_at, timezone),
            remaining=_remaining_capacity(slot),
        )
        for slot in exposed
    ]
    has_more = total_available > len(slot_items)
    return SlotExposureResponse(
        date=query.date,
        person_id=query.person_id,
        total_available=total_available,
        has_more=has_more,
        exposed_slots=slot_items,
    )


@app.post(
    "/v1/bookings",
    response_model=BookingResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["User"],
)
async def create_booking(
    booking_request: BookingRequest,
    idempotency_key: str = Depends(get_idempotency_key),
    request: Request = None,
):
    await store.expire_holds()
    existing = await store.get_idempotent_booking(idempotency_key)
    if existing:
        return BookingResponse(
            booking_id=existing.id,
            status=existing.status,
            slot_id=existing.slot_id,
            hold_expires_at=existing.hold_expires_at,
        )
    hold_ttl = timedelta(minutes=10)
    try:
        booking = await store.create_booking_hold(
            slot_id=booking_request.slot_id,
            idempotency_key=idempotency_key,
            user_id=request.headers.get("X-User-Id") if request else None,
            customer_name=booking_request.customer.name,
            customer_phone=booking_request.customer.phone,
            customer_email=booking_request.customer.email,
            notes=booking_request.notes,
            consent=booking_request.consent,
            source=booking_request.source,
            hold_ttl=hold_ttl,
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except SlotFullError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "SLOT_FULL", "message": "Selected time is no longer available."},
        )
    return BookingResponse(
        booking_id=booking.id,
        status=booking.status,
        slot_id=booking.slot_id,
        hold_expires_at=booking.hold_expires_at,
    )


@app.post(
    "/v1/bookings/{booking_id}/confirm",
    response_model=BookingConfirmResponse,
    tags=["User"],
)
async def confirm_booking(booking_id: str):
    await store.expire_holds()
    try:
        booking = await store.confirm_booking(booking_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except HoldExpiredError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return BookingConfirmResponse(booking_id=booking.id, status=booking.status, slot_id=booking.slot_id)


@app.post(
    "/admin/v1/availabilities",
    response_model=AvailabilityRuleResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Admin"],
)
async def create_availability_rule(payload: AvailabilityRulePayload):
    try:
        await store.get_location(payload.location_id)
        if payload.person_id:
            await store.get_person(payload.person_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    rule_id = str(uuid.uuid4())
    availability_rule = AvailabilityRule(
        id=rule_id,
        location_id=payload.location_id,
        person_id=payload.person_id,
        rule_kind=payload.rule_kind,
        days_of_week=payload.days_of_week,
        start_time=payload.start_time,
        end_time=payload.end_time,
        slot_capacity=payload.slot_capacity,
        slot_granularity_minutes=payload.slot_granularity_minutes,
        slot_duration_minutes=payload.slot_duration_minutes,
        valid_from=payload.valid_from,
        valid_to=payload.valid_to,
        is_closed=payload.is_closed,
    )
    await store.add_availability_rule(availability_rule)
    return AvailabilityRuleResponse(rule_id=rule_id)


@app.post(
    "/admin/v1/slots/generate",
    response_model=SlotGenerationResponse,
    tags=["Admin"],
)
async def generate_slots(payload: SlotGenerationRequest):
    try:
        location = await store.get_location(payload.location_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    created, skipped = await _generate_slots_for_range(
        location_id=payload.location_id,
        location_timezone=location.timezone,
        start_date=payload.from_date,
        end_date=payload.to_date,
        dry_run=payload.dry_run,
    )
    return SlotGenerationResponse(created=created, skipped=skipped)


@app.get("/admin/v1/bookings", response_model=BookingListResponse, tags=["Admin"])
async def list_bookings(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    status_filter: Optional[str] = Query(default=None, alias="status"),
    person_id: Optional[str] = None,
    location_id: Optional[str] = None,
    date_from: Optional[date] = Query(default=None, alias="date_from"),
    date_to: Optional[date] = Query(default=None, alias="date_to"),
    q: Optional[str] = None,
):
    await store.expire_holds()
    records = await store.list_bookings()
    location_ids = {record.slot.location_id for record in records}
    locations_cache: dict[str, Location] = {}
    for loc_id in location_ids:
        try:
            locations_cache[loc_id] = await store.get_location(loc_id)
        except NotFoundError:
            continue
    filtered: list[BookingWithSlot] = []
    for record in records:
        booking = record.booking
        slot = record.slot
        if status_filter and booking.status != status_filter:
            continue
        if person_id and slot.person_id != person_id:
            continue
        if location_id and slot.location_id != location_id:
            continue
        if date_from and slot.date < date_from:
            continue
        if date_to and slot.date > date_to:
            continue
        if q and q not in {booking.customer_phone, booking.customer_email or ""}:
            continue
        filtered.append(record)

    total = len(filtered)
    start_index = (page - 1) * page_size
    end_index = start_index + page_size
    page_items = filtered[start_index:end_index]

    items = []
    for record in page_items:
        booking = record.booking
        slot = record.slot
        location = locations_cache.get(slot.location_id)
        start_at = _to_timezone(slot.start_at, location.timezone) if location else slot.start_at
        items.append(
            BookingListItem(
                booking_id=booking.id,
                status=booking.status,
                date=slot.date,
                start_at=start_at,
                customer={
                    "name": booking.customer_name,
                    "phone": booking.customer_phone,
                    "email": booking.customer_email,
                },
                person_id=slot.person_id,
            )
        )

    return BookingListResponse(page=page, page_size=page_size, total=total, items=items)


@app.patch(
    "/admin/v1/bookings/{booking_id}",
    response_model=BookingPatchResponse,
    tags=["Admin"],
)
async def update_booking(booking_id: str, payload: BookingPatchRequest):
    await store.expire_holds()
    if payload.action == "cancel":
        try:
            booking = await store.cancel_booking(booking_id)
        except NotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        return BookingPatchResponse(booking_id=booking.id, status=booking.status, slot_id=booking.slot_id)

    try:
        booking = await store.reschedule_booking(booking_id, payload.new_slot_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except SlotFullError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return BookingPatchResponse(booking_id=booking.id, status=booking.status, slot_id=booking.slot_id)


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
        tags=tags_metadata,
    )
    app.openapi_schema = openapi_schema
    return app.openapi_schema


app.openapi = custom_openapi  # type: ignore[assignment]


@app.get("/openapi.yaml", include_in_schema=False)
async def openapi_yaml() -> Response:
    schema = custom_openapi()
    return Response(
        content=yaml.safe_dump(schema, sort_keys=False),
        media_type="application/yaml",
    )


def _remaining_capacity(slot: SlotInstance) -> int:
    return slot.capacity - (slot.booked + slot.hold)


def _to_timezone(dt: datetime, tz_name: str) -> datetime:
    tz = ZoneInfo(tz_name)
    return dt.astimezone(tz)


def _daterange(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


async def _generate_slots_for_range(
    *,
    location_id: str,
    location_timezone: str,
    start_date: date,
    end_date: date,
    dry_run: bool = False,
) -> tuple[int, int]:
    rules = await store.list_availability_rules(location_id)
    if not rules:
        return 0, 0
    existing_slots = await store.list_slots(
        location_id=location_id,
        start_date=start_date,
        end_date=end_date,
    )
    existing_index = {
        (slot.person_id, slot.start_at): slot for slot in existing_slots
    }
    tz = ZoneInfo(location_timezone)
    utc = ZoneInfo("UTC")
    created = 0
    skipped = 0
    iso_cache: dict[str, set[int]] = {}
    for single_date in _daterange(start_date, end_date):
        for rule in rules:
            if rule.days_of_week:
                cache_key = "-".join(rule.days_of_week)
                iso_days = iso_cache.get(cache_key)
                if iso_days is None:
                    iso_days = day_names_to_iso(rule.days_of_week)
                    iso_cache[cache_key] = iso_days
                if iso_days and single_date.isoweekday() not in iso_days:
                    continue
            if rule.valid_from and single_date < rule.valid_from:
                continue
            if rule.valid_to and single_date > rule.valid_to:
                continue
            if rule.is_closed:
                continue
            duration = rule.slot_duration_minutes
            start_dt_local = datetime.combine(single_date, rule.start_time, tzinfo=tz)
            end_dt_local = datetime.combine(single_date, rule.end_time, tzinfo=tz)
            cursor = start_dt_local
            while cursor + timedelta(minutes=duration) <= end_dt_local:
                start_utc = cursor.astimezone(utc)
                end_utc = (cursor + timedelta(minutes=duration)).astimezone(utc)
                key = (rule.person_id, start_utc)
                if key in existing_index:
                    skipped += 1
                else:
                    if dry_run:
                        created += 1
                    else:
                        slot = SlotInstance(
                            id=str(uuid.uuid4()),
                            location_id=rule.location_id,
                            person_id=rule.person_id,
                            date=single_date,
                            start_at=start_utc,
                            end_at=end_utc,
                            capacity=rule.slot_capacity,
                            booked=0,
                            hold=0,
                            status="open",
                        )
                        created_flag, persisted = await store.add_slot_instance(slot)
                        if created_flag:
                            created += 1
                            existing_index[key] = persisted
                        else:
                            skipped += 1
                cursor += timedelta(minutes=rule.slot_granularity_minutes)
    return created, skipped


@app.exception_handler(ValueError)
async def value_error_handler(_: Request, exc: ValueError):
    return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, content={"detail": str(exc)})


