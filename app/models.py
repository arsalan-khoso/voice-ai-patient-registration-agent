"""Relational schema. Column types and CHECK constraints enforce the data model at the
database level, as a second line of defence behind the Pydantic validation."""
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, String, Text, TypeDecorator, Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    """Always store UTC and always return tz-aware datetimes (SQLite drops tzinfo)."""
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None and value.tzinfo is not None:
            value = value.astimezone(timezone.utc)
        return value

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value


class Patient(Base):
    __tablename__ = "patients"
    __table_args__ = (
        CheckConstraint("sex IN ('Male','Female','Other','Decline to Answer')", name="ck_patients_sex"),
        CheckConstraint("length(phone_number) = 10", name="ck_patients_phone_len"),
        CheckConstraint("emergency_contact_phone IS NULL OR length(emergency_contact_phone) = 10",
                        name="ck_patients_ec_phone_len"),
        CheckConstraint("length(state) = 2", name="ck_patients_state_len"),
        CheckConstraint("length(first_name) >= 1 AND length(last_name) >= 1", name="ck_patients_name_nonempty"),
        Index("ix_patients_last_name", "last_name"),
        Index("ix_patients_phone_number", "phone_number"),
        Index("ix_patients_date_of_birth", "date_of_birth"),
    )

    patient_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    first_name: Mapped[str] = mapped_column(String(50))
    last_name: Mapped[str] = mapped_column(String(50))
    date_of_birth: Mapped[date] = mapped_column(Date)
    sex: Mapped[str] = mapped_column(String(20))
    phone_number: Mapped[str] = mapped_column(String(10))  # 10 digits, no punctuation
    email: Mapped[str | None] = mapped_column(String(254))
    address_line_1: Mapped[str] = mapped_column(String(200))
    address_line_2: Mapped[str | None] = mapped_column(String(100))
    city: Mapped[str] = mapped_column(String(100))
    state: Mapped[str] = mapped_column(String(2))
    zip_code: Mapped[str] = mapped_column(String(10))  # 12345 or 12345-6789
    insurance_provider: Mapped[str | None] = mapped_column(String(100))
    insurance_member_id: Mapped[str | None] = mapped_column(String(50))
    preferred_language: Mapped[str] = mapped_column(String(50), default="English", server_default="English")
    emergency_contact_name: Mapped[str | None] = mapped_column(String(100))
    emergency_contact_phone: Mapped[str | None] = mapped_column(String(10))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)  # soft delete marker


class CallLog(Base):
    """One row per phone call: links a call to the patient it produced + keeps the transcript."""
    __tablename__ = "call_logs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    vapi_call_id: Mapped[str] = mapped_column(String(100), unique=True)
    patient_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("patients.patient_id"), index=True)
    caller_number: Mapped[str | None] = mapped_column(String(20))
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    ended_reason: Mapped[str | None] = mapped_column(String(100))
    summary: Mapped[str | None] = mapped_column(Text)
    transcript: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
