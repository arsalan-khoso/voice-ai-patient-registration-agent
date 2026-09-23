"""Call log service: ties each phone call to the patient it created and stores the transcript."""
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CallLog


def get_or_create_call(db: Session, vapi_call_id: str, caller_number: str | None = None) -> CallLog:
    call = db.scalars(select(CallLog).where(CallLog.vapi_call_id == vapi_call_id)).first()
    if call is None:
        call = CallLog(vapi_call_id=vapi_call_id, caller_number=caller_number)
        db.add(call)
        db.flush()
    return call


def link_patient(db: Session, vapi_call_id: str, patient_id: uuid.UUID, caller_number: str | None = None) -> None:
    call = get_or_create_call(db, vapi_call_id, caller_number)
    call.patient_id = patient_id
    db.commit()


def patient_for_call(db: Session, vapi_call_id: str) -> uuid.UUID | None:
    return db.scalar(select(CallLog.patient_id).where(CallLog.vapi_call_id == vapi_call_id))


def record_call_report(
    db: Session,
    vapi_call_id: str,
    *,
    caller_number: str | None,
    started_at: datetime | None,
    ended_at: datetime | None,
    duration_seconds: int | None,
    ended_reason: str | None,
    summary: str | None,
    transcript: str | None,
) -> CallLog:
    call = get_or_create_call(db, vapi_call_id, caller_number)
    call.caller_number = call.caller_number or caller_number
    call.started_at, call.ended_at = started_at, ended_at
    call.duration_seconds, call.ended_reason = duration_seconds, ended_reason
    call.summary, call.transcript = summary, transcript
    db.commit()
    return call


def list_calls_for_patient(db: Session, patient_id: uuid.UUID) -> list[CallLog]:
    return list(db.scalars(
        select(CallLog).where(CallLog.patient_id == patient_id).order_by(CallLog.created_at.desc())
    ).all())
