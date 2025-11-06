from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo


@dataclass
class Location:
    id: str
    name: str
    timezone: str


@dataclass
class Service:
    id: str
    location_id: str
    name: str
    duration_minutes: int
    requires_stylist: bool = True
    active: bool = True


@dataclass
class Stylist:
    id: str
    location_id: str
    name: str
    skills: List[str] = field(default_factory=list)
    active: bool = True


@dataclass
class AvailabilityRule:
    id: str
    location_id: str
    stylist_id: Optional[str]
    service_id: Optional[str]
    rule_kind: str
    days_of_week: Optional[List[int]]
    start_time: time
    end_time: time
    slot_capacity: int
    slot_granularity_minutes: int
    valid_from: Optional[date]
    valid_to: Optional[date]
    is_closed: bool


@dataclass
class SlotInstance:
    id: str
    location_id: str
    service_id: str
    stylist_id: Optional[str]
    date: date
    start_at: datetime
    end_at: datetime
    capacity: int
    booked: int = 0
    hold: int = 0
    status: str = "open"

    def refresh_status(self) -> None:
        remaining = self.capacity - (self.booked + self.hold)
        if remaining <= 0:
            self.status = "full"
        elif self.booked > 0:
            self.status = "partial"
        else:
            self.status = "open"


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
    confirmed_at: Optional[datetime] = None
    cancelled_at: Optional[datetime] = None
    expired_at: Optional[datetime] = None
    consent: Optional[dict] = None
    source: Optional[str] = None


class DataStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.locations: Dict[str, Location] = {}
        self.services: Dict[str, Service] = {}
        self.stylists: Dict[str, Stylist] = {}
        self.availability_rules: Dict[str, AvailabilityRule] = {}
        self.slot_instances: Dict[str, SlotInstance] = {}
        self.bookings: Dict[str, Booking] = {}
        self.idempotency_cache: Dict[str, str] = {}
        self._bootstrap()

    def _bootstrap(self) -> None:
        with self._lock:
            location_id = str(uuid.uuid4())
            stylist_id = str(uuid.uuid4())
            service_id = str(uuid.uuid4())
            self.locations[location_id] = Location(
                id=location_id, name="Downtown Studio", timezone="Asia/Dubai"
            )
            self.stylists[stylist_id] = Stylist(
                id=stylist_id, location_id=location_id, name="Aisha"
            )
            self.services[service_id] = Service(
                id=service_id,
                location_id=location_id,
                name="Signature Cut",
                duration_minutes=45,
            )
            rule_id = str(uuid.uuid4())
            self.availability_rules[rule_id] = AvailabilityRule(
                id=rule_id,
                location_id=location_id,
                stylist_id=stylist_id,
                service_id=service_id,
                rule_kind="WEEKLY",
                days_of_week=[1, 2, 3, 4, 5],
                start_time=time(10, 0),
                end_time=time(19, 0),
                slot_capacity=1,
                slot_granularity_minutes=45,
                valid_from=date.today(),
                valid_to=date.today() + timedelta(days=30),
                is_closed=False,
            )

    # Helper methods
    def get_location(self, location_id: str) -> Location:
        location = self.locations.get(location_id)
        if not location:
            raise KeyError("location not found")
        return location

    def get_service(self, service_id: str) -> Service:
        service = self.services.get(service_id)
        if not service:
            raise KeyError("service not found")
        return service

    def get_stylist(self, stylist_id: Optional[str]) -> Optional[Stylist]:
        if stylist_id is None:
            return None
        stylist = self.stylists.get(stylist_id)
        if not stylist:
            raise KeyError("stylist not found")
        return stylist

    def add_availability_rule(self, payload: AvailabilityRule) -> str:
        with self._lock:
            rule_id = payload.id or str(uuid.uuid4())
            payload.id = rule_id
            self.availability_rules[rule_id] = payload
            return rule_id

    def add_slot_instance(self, slot: SlotInstance) -> Tuple[bool, SlotInstance]:
        key = (slot.location_id, slot.service_id, slot.stylist_id, slot.start_at)
        with self._lock:
            for existing in self.slot_instances.values():
                existing_key = (
                    existing.location_id,
                    existing.service_id,
                    existing.stylist_id,
                    existing.start_at,
                )
                if existing_key == key:
                    return False, existing
            self.slot_instances[slot.id] = slot
            return True, slot

    def list_slots(
        self,
        location_id: str,
        service_id: str,
        stylist_id: Optional[str],
        for_date: date,
    ) -> List[SlotInstance]:
        with self._lock:
            return [
                slot
                for slot in self.slot_instances.values()
                if slot.location_id == location_id
                and slot.service_id == service_id
                and slot.date == for_date
                and (stylist_id is None or slot.stylist_id == stylist_id)
            ]

    def all_slots(self) -> List[SlotInstance]:
        with self._lock:
            return list(self.slot_instances.values())

    def upsert_booking(self, booking: Booking) -> Booking:
        with self._lock:
            self.bookings[booking.id] = booking
            return booking

    def get_booking(self, booking_id: str) -> Booking:
        booking = self.bookings.get(booking_id)
        if not booking:
            raise KeyError("booking not found")
        return booking

    def list_bookings(self) -> List[Booking]:
        with self._lock:
            return list(self.bookings.values())

    def set_idempotency(self, key: str, booking_id: str) -> None:
        with self._lock:
            self.idempotency_cache[key] = booking_id

    def get_idempotent_booking(self, key: str) -> Optional[Booking]:
        with self._lock:
            booking_id = self.idempotency_cache.get(key)
            if booking_id:
                return self.bookings.get(booking_id)
            return None

    def expire_holds(self, now: Optional[datetime] = None) -> None:
        now = now or datetime.utcnow().replace(tzinfo=ZoneInfo("UTC"))
        with self._lock:
            for booking in self.bookings.values():
                if booking.status == "held" and booking.hold_expires_at and booking.hold_expires_at <= now:
                    slot = self.slot_instances.get(booking.slot_id)
                    if slot:
                        slot.hold = max(0, slot.hold - 1)
                        slot.refresh_status()
                    booking.status = "expired"
                    booking.expired_at = now

    def find_slot(self, slot_id: str) -> SlotInstance:
        slot = self.slot_instances.get(slot_id)
        if not slot:
            raise KeyError("slot not found")
        return slot

    def update_slot(self, slot: SlotInstance) -> None:
        with self._lock:
            self.slot_instances[slot.id] = slot

    def ensure_seed_data(self) -> Tuple[str, str, str]:
        # returns location_id, service_id, stylist_id
        with self._lock:
            location = next(iter(self.locations.values()))
            service = next(iter(self.services.values()))
            stylist = next(iter(self.stylists.values()))
            return location.id, service.id, stylist.id


store = DataStore()
