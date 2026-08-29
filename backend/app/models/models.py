"""
Core database schema. UUID primary keys, explicit status enums, and
unique constraints wherever duplicates would be a real problem (see
services/db_utils.py and services/matching.py for how these are used
race-safely).
"""
import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean, DateTime, Enum, ForeignKey, Integer, String, Text,
    UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


def gen_uuid() -> uuid.UUID:
    return uuid.uuid4()


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class UserRole(str, enum.Enum):
    CARRIER = "CARRIER"
    SHIPPER = "SHIPPER"
    ADMIN = "ADMIN"
    UNSET = "UNSET"  # registered via /start but hasn't picked a role yet


class TruckStatus(str, enum.Enum):
    AVAILABLE = "AVAILABLE"
    ON_TRIP = "ON_TRIP"
    ARRIVING_SOON = "ARRIVING_SOON"
    UNAVAILABLE = "UNAVAILABLE"


class LoadStatus(str, enum.Enum):
    NEW = "NEW"
    WAITING_FOR_MATCH = "WAITING_FOR_MATCH"
    MATCHED = "MATCHED"
    ADMIN_CONTACTING = "ADMIN_CONTACTING"
    CONNECTED = "CONNECTED"
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"


class MatchStatus(str, enum.Enum):
    PENDING = "PENDING"
    INTERESTED = "INTERESTED"
    ADMIN_CONTACTING = "ADMIN_CONTACTING"
    CONNECTED = "CONNECTED"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class NotificationType(str, enum.Enum):
    MATCH_FOUND = "MATCH_FOUND"
    BROADCAST = "BROADCAST"
    REMINDER = "REMINDER"
    PROACTIVE_MATCH = "PROACTIVE_MATCH"
    INTEREST_RECEIVED = "INTEREST_RECEIVED"
    # Sent when a match is auto-connected because the two people behind
    # it are a TrustedPair (see below) -- no new contact info is being
    # exposed that they don't already have from a prior admin-mediated
    # connection, only the friction of re-approving is skipped.
    AUTO_CONNECTED = "AUTO_CONNECTED"


class NotificationStatus(str, enum.Enum):
    SENT = "SENT"
    VIEWED = "VIEWED"
    INTERESTED = "INTERESTED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"


class InterestStatus(str, enum.Enum):
    INTERESTED = "INTERESTED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    telegram_id: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=True)
    phone: Mapped[str] = mapped_column(String, nullable=True)
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), default=UserRole.UNSET, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    trucks: Mapped[list["Truck"]] = relationship(back_populates="owner")
    loads: Mapped[list["Load"]] = relationship(back_populates="owner")


class Truck(Base, TimestampMixin):
    __tablename__ = "trucks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)

    truck_type: Mapped[str] = mapped_column(String, nullable=True)
    current_city: Mapped[str] = mapped_column(String, nullable=False)
    desired_destination: Mapped[str] = mapped_column(String, nullable=True)

    status: Mapped[TruckStatus] = mapped_column(Enum(TruckStatus), default=TruckStatus.AVAILABLE)
    available: Mapped[bool] = mapped_column(Boolean, default=True)

    trip_origin: Mapped[str] = mapped_column(String, nullable=True)
    trip_destination: Mapped[str] = mapped_column(String, nullable=True)
    trip_eta: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    proactive_scan_done: Mapped[bool] = mapped_column(Boolean, default=False)

    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)

    owner: Mapped["User"] = relationship(back_populates="trucks")


class Load(Base, TimestampMixin):
    __tablename__ = "loads"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)

    origin_city: Mapped[str] = mapped_column(String, nullable=False)
    destination_city: Mapped[str] = mapped_column(String, nullable=False)
    truck_type: Mapped[str] = mapped_column(String, nullable=True)
    truck_count: Mapped[int] = mapped_column(Integer, default=1)
    loading_time: Mapped[str] = mapped_column(String, nullable=True)
    notes: Mapped[str] = mapped_column(Text, nullable=True)
    raw_text: Mapped[str] = mapped_column(Text, nullable=True)

    status: Mapped[LoadStatus] = mapped_column(Enum(LoadStatus), default=LoadStatus.NEW)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)

    owner: Mapped["User"] = relationship(back_populates="loads")
    matches: Mapped[list["Match"]] = relationship(back_populates="load")


class Match(Base, TimestampMixin):
    __tablename__ = "matches"
    __table_args__ = (
        UniqueConstraint("load_id", "truck_id", name="uq_matches_load_truck"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    load_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("loads.id"), nullable=False)
    truck_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("trucks.id"), nullable=False)

    score: Mapped[int] = mapped_column(Integer, default=0)
    reason: Mapped[str] = mapped_column(Text, nullable=True)
    is_proactive: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[MatchStatus] = mapped_column(Enum(MatchStatus), default=MatchStatus.PENDING)

    load: Mapped["Load"] = relationship(back_populates="matches")


class Notification(Base, TimestampMixin):
    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint("user_id", "load_id", "notification_type", name="uq_notif_user_load_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    load_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("loads.id"), nullable=False)
    truck_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("trucks.id"), nullable=True)
    match_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("matches.id"), nullable=True)

    notification_type: Mapped[NotificationType] = mapped_column(Enum(NotificationType), nullable=False)
    status: Mapped[NotificationStatus] = mapped_column(Enum(NotificationStatus), default=NotificationStatus.SENT)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reminder_sent: Mapped[bool] = mapped_column(Boolean, default=False)


class Interest(Base, TimestampMixin):
    __tablename__ = "interests"
    __table_args__ = (
        UniqueConstraint("load_id", "truck_id", name="uq_interest_load_truck"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    load_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("loads.id"), nullable=False)
    truck_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("trucks.id"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    status: Mapped[InterestStatus] = mapped_column(Enum(InterestStatus), default=InterestStatus.INTERESTED)


class TrustedPair(Base, TimestampMixin):
    """
    Records that the admin has manually connected this specific shipper
    and this specific carrier at least once. Any FUTURE match between
    the same two users is auto-connected without waiting on admin
    action -- they've already exchanged contact info under admin
    supervision before, so revealing it again isn't a new privacy
    exposure, it's just removing repeated friction for a relationship
    the admin already vetted. Created only from the admin's "connect"
    action (see api/admin.py) -- never automatically inferred, and
    never creatable by either party themselves.
    """
    __tablename__ = "trusted_pairs"
    __table_args__ = (
        UniqueConstraint("shipper_user_id", "carrier_user_id", name="uq_trusted_pair"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    shipper_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    carrier_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)


class AuditLog(Base, TimestampMixin):
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    actor_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=True)
    action: Mapped[str] = mapped_column(String, nullable=False)
    entity_type: Mapped[str] = mapped_column(String, nullable=True)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=True)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=True)
