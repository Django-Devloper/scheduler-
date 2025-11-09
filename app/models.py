from __future__ import annotations

import os

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    SmallInteger,
    String,
    Table,
    Text,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID


SCHEDULER_SCHEMA = os.getenv("SCHEDULER_DB_SCHEMA", "scheduler")

metadata = MetaData(schema=SCHEDULER_SCHEMA)


locations = Table(
    "locations",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column(
        "biz_entity_id",
        UUID(as_uuid=True),
        ForeignKey(
            "public.biz_entity.id",
            name="fk_locations_biz_entity",
            link_to_name=True,
        ),
        nullable=True,
    ),
    Column("name", String(255), nullable=False),
    Column("timezone", String(64), nullable=False, server_default="Asia/Dubai"),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    Column("updated_at", DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False),
)


services = Table(
    "services",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column(
        "location_id",
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEDULER_SCHEMA}.locations.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("name", String(255), nullable=False),
    Column("duration_minutes", Integer, nullable=False),
    Column("requires_stylist", Boolean, nullable=False, server_default="true"),
    Column("active", Boolean, nullable=False, server_default="true"),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    Column("updated_at", DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False),
    CheckConstraint("duration_minutes > 0", name="services_duration_positive"),
)


stylists = Table(
    "stylists",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column(
        "location_id",
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEDULER_SCHEMA}.locations.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("name", String(255), nullable=False),
    Column("skills", JSON, nullable=True),
    Column("active", Boolean, nullable=False, server_default="true"),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    Column("updated_at", DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False),
)


availability_rules = Table(
    "availability_rules",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column(
        "location_id",
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEDULER_SCHEMA}.locations.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "stylist_id",
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEDULER_SCHEMA}.stylists.id", ondelete="SET NULL"),
        nullable=True,
    ),
    Column(
        "service_id",
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEDULER_SCHEMA}.services.id", ondelete="SET NULL"),
        nullable=True,
    ),
    Column("rule_kind", String(32), nullable=False),
    Column("days_of_week", JSON, nullable=True),
    Column("start_time", Time(timezone=False), nullable=False),
    Column("end_time", Time(timezone=False), nullable=False),
    Column("slot_capacity", SmallInteger, nullable=False, server_default="1"),
    Column("slot_granularity_minutes", SmallInteger, nullable=False, server_default="15"),
    Column("valid_from", Date, nullable=True),
    Column("valid_to", Date, nullable=True),
    Column("is_closed", Boolean, nullable=False, server_default="false"),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    Column("updated_at", DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False),
    CheckConstraint("slot_capacity > 0", name="availability_capacity_positive"),
    CheckConstraint("slot_granularity_minutes > 0", name="availability_granularity_positive"),
)


slot_instances = Table(
    "slot_instances",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column(
        "location_id",
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEDULER_SCHEMA}.locations.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "service_id",
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEDULER_SCHEMA}.services.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "stylist_id",
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEDULER_SCHEMA}.stylists.id", ondelete="SET NULL"),
        nullable=True,
    ),
    Column("date", Date, nullable=False),
    Column("start_at", DateTime(timezone=True), nullable=False),
    Column("end_at", DateTime(timezone=True), nullable=False),
    Column("capacity", SmallInteger, nullable=False),
    Column("booked", SmallInteger, nullable=False, server_default="0"),
    Column("hold", SmallInteger, nullable=False, server_default="0"),
    Column("status", String(32), nullable=False, server_default="open"),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    Column("updated_at", DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False),
    CheckConstraint("capacity > 0", name="slot_capacity_positive"),
    UniqueConstraint(
        "location_id",
        "service_id",
        "stylist_id",
        "start_at",
        name="uq_slot_instances_natural",
    ),
)


bookings = Table(
    "bookings",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column(
        "slot_id",
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEDULER_SCHEMA}.slot_instances.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "user_id",
        UUID(as_uuid=True),
        ForeignKey(
            "public.auth_user.id",
            name="fk_bookings_auth_user",
            link_to_name=True,
        ),
        nullable=True,
    ),
    Column("customer_name", String(255), nullable=False),
    Column("customer_phone", String(50), nullable=False),
    Column("customer_email", String(255), nullable=True),
    Column("notes", Text, nullable=True),
    Column("status", String(32), nullable=False),
    Column("hold_expires_at", DateTime(timezone=True), nullable=True),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    Column("confirmed_at", DateTime(timezone=True), nullable=True),
    Column("cancelled_at", DateTime(timezone=True), nullable=True),
    Column("expired_at", DateTime(timezone=True), nullable=True),
    Column("consent", JSON, nullable=True),
    Column("source", String(64), nullable=True),
)


idempotency_keys = Table(
    "idempotency_keys",
    metadata,
    Column("idempotency_key", String(255), primary_key=True),
    Column(
        "booking_id",
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEDULER_SCHEMA}.bookings.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
)
