from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Optional, Sequence

from zoneinfo import ZoneInfo

from sqlalchemy import Select, and_, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from .db import get_session, initialize_schema
from .days import decode_day_list, normalize_day_list
from .models import (
    availability_rules,
    bookings,
    idempotency_keys,
    locations,
    people,
    slot_instances,
)


UTC = ZoneInfo("UTC")


class NotFoundError(KeyError):
    pass


class SlotFullError(RuntimeError):
    pass


class HoldExpiredError(RuntimeError):
    pass


@dataclass
class Location:
    id: str
    name: str
    timezone: str
    biz_entity_id: Optional[str] = None


@dataclass
class Person:
    id: str
    location_id: str
    name: str
    skills: Optional[Sequence[str]]
    active: bool


@dataclass
class AvailabilityRule:
    id: str
    location_id: str
    person_id: Optional[str]
    rule_kind: str
    days_of_week: Optional[Sequence[str]]
    start_time: time
    end_time: time
    slot_capacity: int
    slot_granularity_minutes: int
    slot_duration_minutes: int
    valid_from: Optional[date]
    valid_to: Optional[date]
    is_closed: bool


@dataclass
class SlotInstance:
    id: str
    location_id: str
    person_id: Optional[str]
    date: date
    start_at: datetime
    end_at: datetime
    capacity: int
    booked: int
    hold: int
    status: str

    def remaining(self) -> int:
        return self.capacity - (self.booked + self.hold)


@dataclass
class Booking:
    id: str
    slot_id: str
    user_id: Optional[str]
    customer_name: str
    customer_phone: str
    customer_email: Optional[str]
    notes: Optional[str]
    status: str
    hold_expires_at: Optional[datetime]
    created_at: datetime
    confirmed_at: Optional[datetime]
    cancelled_at: Optional[datetime]
    expired_at: Optional[datetime]
    consent: Optional[dict]
    source: Optional[str]


@dataclass
class BookingWithSlot:
    booking: Booking
    slot: SlotInstance


class DatabaseStore:
    def __init__(self) -> None:
        self._initialized = False

    async def initialize(self) -> None:
        if self._initialized:
            return
        await initialize_schema()
        self._initialized = True

    async def expire_holds(self, now: Optional[datetime] = None) -> None:
        now = now or datetime.utcnow().replace(tzinfo=UTC)
        async with get_session() as session:
            async with session.begin():
                expiring = await session.execute(
                    select(bookings.c.id, bookings.c.slot_id)
                    .where(
                        bookings.c.status == "held",
                        bookings.c.hold_expires_at.isnot(None),
                        bookings.c.hold_expires_at <= now,
                    )
                    .with_for_update(skip_locked=True)
                )
                rows = expiring.mappings().all()
                for row in rows:
                    slot_result = await session.execute(
                        select(
                            slot_instances.c.id,
                            slot_instances.c.capacity,
                            slot_instances.c.booked,
                            slot_instances.c.hold,
                            slot_instances.c.status,
                        )
                        .where(slot_instances.c.id == row["slot_id"])
                        .with_for_update()
                    )
                    slot_row = slot_result.mappings().first()
                    if not slot_row:
                        continue
                    new_hold = max(0, slot_row["hold"] - 1)
                    new_status = _derive_slot_status(
                        capacity=slot_row["capacity"],
                        booked=slot_row["booked"],
                        hold=new_hold,
                        current_status=slot_row["status"],
                    )
                    await session.execute(
                        update(slot_instances)
                        .where(slot_instances.c.id == slot_row["id"])
                        .values(hold=new_hold, status=new_status, updated_at=func.now())
                    )
                    await session.execute(
                        update(bookings)
                        .where(bookings.c.id == row["id"])
                        .values(status="expired", expired_at=now)
                    )

    async def get_location(self, location_id: str) -> Location:
        async with get_session() as session:
            result = await session.execute(
                select(
                    locations.c.id,
                    locations.c.name,
                    locations.c.timezone,
                    locations.c.biz_entity_id,
                ).where(locations.c.id == location_id)
            )
            row = result.mappings().first()
            if not row:
                raise NotFoundError("location not found")
            return Location(
                id=str(row["id"]),
                name=row["name"],
                timezone=row["timezone"],
                biz_entity_id=str(row["biz_entity_id"]) if row["biz_entity_id"] else None,
            )

    async def get_person(self, person_id: Optional[str]) -> Optional[Person]:
        if person_id is None:
            return None
        async with get_session() as session:
            result = await session.execute(
                select(
                    people.c.id,
                    people.c.location_id,
                    people.c.name,
                    people.c.skills,
                    people.c.active,
                ).where(people.c.id == person_id)
            )
            row = result.mappings().first()
            if not row:
                raise NotFoundError("person not found")
            return Person(
                id=str(row["id"]),
                location_id=str(row["location_id"]),
                name=row["name"],
                skills=row["skills"],
                active=bool(row["active"]),
            )

    async def list_slots(
        self,
        *,
        location_id: Optional[str] = None,
        person_id: Optional[str] = None,
        for_date: Optional[date] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> list[SlotInstance]:
        conditions = []
        if location_id:
            conditions.append(slot_instances.c.location_id == location_id)
        if person_id is not None:
            conditions.append(slot_instances.c.person_id == person_id)
        if for_date:
            conditions.append(slot_instances.c.date == for_date)
        if start_date:
            conditions.append(slot_instances.c.date >= start_date)
        if end_date:
            conditions.append(slot_instances.c.date <= end_date)

        stmt: Select = select(slot_instances)
        if conditions:
            stmt = stmt.where(and_(*conditions))
        stmt = stmt.order_by(slot_instances.c.start_at)

        async with get_session() as session:
            result = await session.execute(stmt)
            rows = result.mappings().all()
        return [_map_slot(row) for row in rows]

    async def add_availability_rule(self, rule: AvailabilityRule) -> str:
        normalized_days = (
            list(normalize_day_list(rule.days_of_week)) if rule.days_of_week else None
        )
        async with get_session() as session:
            async with session.begin():
                await session.execute(
                    pg_insert(availability_rules)
                    .values(
                        id=rule.id,
                        location_id=rule.location_id,
                        person_id=rule.person_id,
                        rule_kind=rule.rule_kind,
                        days_of_week=normalized_days,
                        start_time=rule.start_time,
                        end_time=rule.end_time,
                        slot_capacity=rule.slot_capacity,
                        slot_granularity_minutes=rule.slot_granularity_minutes,
                        slot_duration_minutes=rule.slot_duration_minutes,
                        valid_from=rule.valid_from,
                        valid_to=rule.valid_to,
                        is_closed=rule.is_closed,
                    )
                    .on_conflict_do_update(
                        index_elements=[availability_rules.c.id],
                        set_={
                            "person_id": rule.person_id,
                            "rule_kind": rule.rule_kind,
                            "days_of_week": normalized_days,
                            "start_time": rule.start_time,
                            "end_time": rule.end_time,
                            "slot_capacity": rule.slot_capacity,
                            "slot_granularity_minutes": rule.slot_granularity_minutes,
                            "slot_duration_minutes": rule.slot_duration_minutes,
                            "valid_from": rule.valid_from,
                            "valid_to": rule.valid_to,
                            "is_closed": rule.is_closed,
                            "updated_at": func.now(),
                        },
                    )
                )
        return rule.id

    async def list_availability_rules(self, location_id: str) -> list[AvailabilityRule]:
        async with get_session() as session:
            result = await session.execute(
                select(availability_rules).where(availability_rules.c.location_id == location_id)
            )
            rows = result.mappings().all()
        return [_map_rule(row) for row in rows]

    async def add_slot_instance(self, slot: SlotInstance) -> tuple[bool, SlotInstance]:
        async with get_session() as session:
            async with session.begin():
                stmt = (
                    pg_insert(slot_instances)
                    .values(
                        id=slot.id,
                        location_id=slot.location_id,
                        person_id=slot.person_id,
                        date=slot.date,
                        start_at=slot.start_at,
                        end_at=slot.end_at,
                        capacity=slot.capacity,
                        booked=slot.booked,
                        hold=slot.hold,
                        status=slot.status,
                    )
                    .on_conflict_do_nothing()
                    .returning(*slot_instances.c)
                )
                result = await session.execute(stmt)
                inserted = result.mappings().first()
                if inserted:
                    return True, _map_slot(inserted)
                conditions = [
                    slot_instances.c.location_id == slot.location_id,
                    slot_instances.c.start_at == slot.start_at,
                ]
                if slot.person_id is None:
                    conditions.append(slot_instances.c.person_id.is_(None))
                else:
                    conditions.append(slot_instances.c.person_id == slot.person_id)
                existing_stmt = select(slot_instances).where(*conditions)
                existing_result = await session.execute(existing_stmt)
                existing_row = existing_result.mappings().first()
                if not existing_row:
                    raise RuntimeError("slot insertion lost")
                return False, _map_slot(existing_row)

    async def find_slot(self, slot_id: str, *, for_update: bool = False) -> SlotInstance:
        stmt = select(slot_instances).where(slot_instances.c.id == slot_id)
        if for_update:
            stmt = stmt.with_for_update()
        async with get_session() as session:
            result = await session.execute(stmt)
            row = result.mappings().first()
            if not row:
                raise NotFoundError("slot not found")
            return _map_slot(row)

    async def get_idempotent_booking(self, key: str) -> Optional[Booking]:
        async with get_session() as session:
            result = await session.execute(
                select(bookings)
                .join(idempotency_keys, bookings.c.id == idempotency_keys.c.booking_id)
                .where(idempotency_keys.c.idempotency_key == key)
            )
            row = result.mappings().first()
            if not row:
                return None
            return _map_booking(row)

    async def create_booking_hold(
        self,
        *,
        slot_id: str,
        idempotency_key: str,
        user_id: Optional[str],
        customer_name: str,
        customer_phone: str,
        customer_email: Optional[str],
        notes: Optional[str],
        consent: Optional[dict],
        source: Optional[str],
        hold_ttl: timedelta,
    ) -> Booking:
        now = datetime.utcnow().replace(tzinfo=UTC)
        existing = await self.get_idempotent_booking(idempotency_key)
        if existing:
            return existing
        async with get_session() as session:
            async with session.begin():
                slot_result = await session.execute(
                    select(slot_instances)
                    .where(slot_instances.c.id == slot_id)
                    .with_for_update()
                )
                slot_row = slot_result.mappings().first()
                if not slot_row:
                    raise NotFoundError("slot not found")
                if slot_row["status"] == "blocked":
                    raise SlotFullError("Selected time is no longer available.")
                remaining = slot_row["capacity"] - (slot_row["booked"] + slot_row["hold"])
                if remaining <= 0:
                    raise SlotFullError("Selected time is no longer available.")

                booking_id = str(uuid.uuid4())
                hold_expires = now + hold_ttl
                await session.execute(
                    pg_insert(bookings)
                    .values(
                        id=booking_id,
                        slot_id=slot_id,
                        user_id=user_id,
                        customer_name=customer_name,
                        customer_phone=customer_phone,
                        customer_email=customer_email,
                        notes=notes,
                        status="held",
                        hold_expires_at=hold_expires,
                        created_at=now,
                        consent=consent,
                        source=source,
                    )
                )

                new_hold = slot_row["hold"] + 1
                new_status = _derive_slot_status(
                    capacity=slot_row["capacity"],
                    booked=slot_row["booked"],
                    hold=new_hold,
                    current_status=slot_row["status"],
                )
                await session.execute(
                    update(slot_instances)
                    .where(slot_instances.c.id == slot_id)
                    .values(hold=new_hold, status=new_status, updated_at=func.now())
                )

                await session.execute(
                    pg_insert(idempotency_keys)
                    .values(idempotency_key=idempotency_key, booking_id=booking_id)
                    .on_conflict_do_nothing()
                )

        return Booking(
            id=booking_id,
            slot_id=slot_id,
            user_id=user_id,
            customer_name=customer_name,
            customer_phone=customer_phone,
            customer_email=customer_email,
            notes=notes,
            status="held",
            hold_expires_at=hold_expires,
            created_at=now,
            confirmed_at=None,
            cancelled_at=None,
            expired_at=None,
            consent=consent,
            source=source,
        )

    async def get_booking(self, booking_id: str, *, for_update: bool = False) -> Booking:
        stmt = select(bookings).where(bookings.c.id == booking_id)
        if for_update:
            stmt = stmt.with_for_update()
        async with get_session() as session:
            result = await session.execute(stmt)
            row = result.mappings().first()
            if not row:
                raise NotFoundError("booking not found")
            return _map_booking(row)

    async def list_bookings(self) -> list[BookingWithSlot]:
        async with get_session() as session:
            result = await session.execute(
                select(
                    bookings.c.id.label("booking_id"),
                    bookings.c.slot_id.label("booking_slot_id"),
                    bookings.c.user_id,
                    bookings.c.customer_name,
                    bookings.c.customer_phone,
                    bookings.c.customer_email,
                    bookings.c.notes,
                    bookings.c.status.label("booking_status"),
                    bookings.c.hold_expires_at,
                    bookings.c.created_at.label("booking_created_at"),
                    bookings.c.confirmed_at,
                    bookings.c.cancelled_at,
                    bookings.c.expired_at,
                    bookings.c.consent,
                    bookings.c.source,
                    slot_instances.c.id.label("slot_id"),
                    slot_instances.c.location_id,
                    slot_instances.c.person_id,
                    slot_instances.c.date,
                    slot_instances.c.start_at,
                    slot_instances.c.end_at,
                    slot_instances.c.capacity,
                    slot_instances.c.booked,
                    slot_instances.c.hold,
                    slot_instances.c.status.label("slot_status"),
                ).join(slot_instances, bookings.c.slot_id == slot_instances.c.id)
            )
            rows = result.mappings().all()
        items: list[BookingWithSlot] = []
        for row in rows:
            booking_data = {
                "id": row["booking_id"],
                "slot_id": row["booking_slot_id"],
                "user_id": row["user_id"],
                "customer_name": row["customer_name"],
                "customer_phone": row["customer_phone"],
                "customer_email": row["customer_email"],
                "notes": row["notes"],
                "status": row["booking_status"],
                "hold_expires_at": row["hold_expires_at"],
                "created_at": row["booking_created_at"],
                "confirmed_at": row["confirmed_at"],
                "cancelled_at": row["cancelled_at"],
                "expired_at": row["expired_at"],
                "consent": row["consent"],
                "source": row["source"],
            }
            slot_data = {
                "id": row["slot_id"],
                "location_id": row["location_id"],
                "person_id": row["person_id"],
                "date": row["date"],
                "start_at": row["start_at"],
                "end_at": row["end_at"],
                "capacity": row["capacity"],
                "booked": row["booked"],
                "hold": row["hold"],
                "status": row["slot_status"],
            }
            items.append(BookingWithSlot(_map_booking(booking_data), _map_slot(slot_data)))
        return items

    async def confirm_booking(self, booking_id: str) -> Booking:
        now = datetime.utcnow().replace(tzinfo=UTC)
        async with get_session() as session:
            async with session.begin():
                booking_result = await session.execute(
                    select(bookings)
                    .where(bookings.c.id == booking_id)
                    .with_for_update()
                )
                booking_row = booking_result.mappings().first()
                if not booking_row:
                    raise NotFoundError("booking not found")
                if booking_row["status"] != "held":
                    raise RuntimeError("Booking is not in held state")
                if booking_row["hold_expires_at"] and booking_row["hold_expires_at"] <= now:
                    raise HoldExpiredError("Hold already expired")

                slot_result = await session.execute(
                    select(slot_instances)
                    .where(slot_instances.c.id == booking_row["slot_id"])
                    .with_for_update()
                )
                slot_row = slot_result.mappings().first()
                if not slot_row:
                    raise NotFoundError("slot not found")
                new_hold = max(0, slot_row["hold"] - 1)
                new_booked = slot_row["booked"] + 1
                new_status = _derive_slot_status(
                    capacity=slot_row["capacity"],
                    booked=new_booked,
                    hold=new_hold,
                    current_status=slot_row["status"],
                )
                await session.execute(
                    update(slot_instances)
                    .where(slot_instances.c.id == slot_row["id"])
                    .values(booked=new_booked, hold=new_hold, status=new_status, updated_at=func.now())
                )
                await session.execute(
                    update(bookings)
                    .where(bookings.c.id == booking_id)
                    .values(status="confirmed", confirmed_at=now)
                )

        return await self.get_booking(booking_id)

    async def cancel_booking(self, booking_id: str) -> Booking:
        now = datetime.utcnow().replace(tzinfo=UTC)
        async with get_session() as session:
            async with session.begin():
                booking_result = await session.execute(
                    select(bookings)
                    .where(bookings.c.id == booking_id)
                    .with_for_update()
                )
                booking_row = booking_result.mappings().first()
                if not booking_row:
                    raise NotFoundError("booking not found")
                status = booking_row["status"]
                if status not in {"held", "confirmed"}:
                    raise RuntimeError("Booking not cancellable")
                slot_result = await session.execute(
                    select(slot_instances)
                    .where(slot_instances.c.id == booking_row["slot_id"])
                    .with_for_update()
                )
                slot_row = slot_result.mappings().first()
                if not slot_row:
                    raise NotFoundError("slot not found")
                new_hold = slot_row["hold"]
                new_booked = slot_row["booked"]
                if status == "held":
                    new_hold = max(0, new_hold - 1)
                elif status == "confirmed":
                    new_booked = max(0, new_booked - 1)
                new_status = _derive_slot_status(
                    capacity=slot_row["capacity"],
                    booked=new_booked,
                    hold=new_hold,
                    current_status=slot_row["status"],
                )
                await session.execute(
                    update(slot_instances)
                    .where(slot_instances.c.id == slot_row["id"])
                    .values(booked=new_booked, hold=new_hold, status=new_status, updated_at=func.now())
                )
                await session.execute(
                    update(bookings)
                    .where(bookings.c.id == booking_id)
                    .values(status="cancelled", cancelled_at=now)
                )

        return await self.get_booking(booking_id)

    async def reschedule_booking(self, booking_id: str, new_slot_id: str) -> Booking:
        async with get_session() as session:
            async with session.begin():
                booking_result = await session.execute(
                    select(bookings)
                    .where(bookings.c.id == booking_id)
                    .with_for_update()
                )
                booking_row = booking_result.mappings().first()
                if not booking_row:
                    raise NotFoundError("booking not found")

                old_slot_result = await session.execute(
                    select(slot_instances)
                    .where(slot_instances.c.id == booking_row["slot_id"])
                    .with_for_update()
                )
                old_slot_row = old_slot_result.mappings().first()
                if not old_slot_row:
                    raise NotFoundError("slot not found")

                new_slot_result = await session.execute(
                    select(slot_instances)
                    .where(slot_instances.c.id == new_slot_id)
                    .with_for_update()
                )
                new_slot_row = new_slot_result.mappings().first()
                if not new_slot_row:
                    raise NotFoundError("slot not found")
                if new_slot_row["status"] == "blocked":
                    raise SlotFullError("New slot has no capacity")
                remaining = new_slot_row["capacity"] - (new_slot_row["booked"] + new_slot_row["hold"])
                if remaining <= 0:
                    raise SlotFullError("New slot has no capacity")

                status = booking_row["status"]
                old_hold = old_slot_row["hold"]
                old_booked = old_slot_row["booked"]
                if status == "held":
                    old_hold = max(0, old_hold - 1)
                elif status == "confirmed":
                    old_booked = max(0, old_booked - 1)
                await session.execute(
                    update(slot_instances)
                    .where(slot_instances.c.id == old_slot_row["id"])
                    .values(
                        hold=old_hold,
                        booked=old_booked,
                        status=_derive_slot_status(
                            capacity=old_slot_row["capacity"],
                            booked=old_booked,
                            hold=old_hold,
                            current_status=old_slot_row["status"],
                        ),
                        updated_at=func.now(),
                    )
                )

                new_hold = new_slot_row["hold"]
                new_booked = new_slot_row["booked"]
                if status == "held":
                    new_hold += 1
                elif status == "confirmed":
                    new_booked += 1
                await session.execute(
                    update(slot_instances)
                    .where(slot_instances.c.id == new_slot_row["id"])
                    .values(
                        hold=new_hold,
                        booked=new_booked,
                        status=_derive_slot_status(
                            capacity=new_slot_row["capacity"],
                            booked=new_booked,
                            hold=new_hold,
                            current_status=new_slot_row["status"],
                        ),
                        updated_at=func.now(),
                    )
                )

                await session.execute(
                    update(bookings)
                    .where(bookings.c.id == booking_id)
                    .values(slot_id=new_slot_id)
                )

        return await self.get_booking(booking_id)


def _derive_slot_status(*, capacity: int, booked: int, hold: int, current_status: str) -> str:
    if current_status == "blocked":
        return "blocked"
    remaining = capacity - (booked + hold)
    if remaining <= 0:
        return "full"
    if booked > 0 or hold > 0:
        return "partial"
    return "open"


def _map_slot(row: dict) -> SlotInstance:
    return SlotInstance(
        id=str(row["id"]),
        location_id=str(row["location_id"]),
        person_id=str(row["person_id"]) if row.get("person_id") else None,
        date=row["date"],
        start_at=row["start_at"],
        end_at=row["end_at"],
        capacity=row["capacity"],
        booked=row["booked"],
        hold=row["hold"],
        status=row["status"],
    )


def _map_booking(row: dict) -> Booking:
    return Booking(
        id=str(row["id"]),
        slot_id=str(row["slot_id"]),
        user_id=str(row["user_id"]) if row.get("user_id") else None,
        customer_name=row["customer_name"],
        customer_phone=row["customer_phone"],
        customer_email=row.get("customer_email"),
        notes=row.get("notes"),
        status=row["status"],
        hold_expires_at=row.get("hold_expires_at"),
        created_at=row["created_at"],
        confirmed_at=row.get("confirmed_at"),
        cancelled_at=row.get("cancelled_at"),
        expired_at=row.get("expired_at"),
        consent=row.get("consent"),
        source=row.get("source"),
    )


def _map_rule(row: dict) -> AvailabilityRule:
    days = row.get("days_of_week")
    if isinstance(days, list):
        normalized_days = decode_day_list(days)
        days_seq: Optional[Sequence[str]] = normalized_days or None
    else:
        days_seq = None
    return AvailabilityRule(
        id=str(row["id"]),
        location_id=str(row["location_id"]),
        person_id=str(row["person_id"]) if row.get("person_id") else None,
        rule_kind=row["rule_kind"],
        days_of_week=days_seq,
        start_time=row["start_time"],
        end_time=row["end_time"],
        slot_capacity=row["slot_capacity"],
        slot_granularity_minutes=row["slot_granularity_minutes"],
        slot_duration_minutes=row["slot_duration_minutes"],
        valid_from=row.get("valid_from"),
        valid_to=row.get("valid_to"),
        is_closed=bool(row["is_closed"]),
    )


store = DatabaseStore()
