"""Patient service layer. The REST routers *and* the voice-agent tools both call these
functions, so there is exactly one code path that touches the patients table."""
import uuid
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Patient, utcnow
from app.schemas import PatientCreate, PatientUpdate


class PatientNotFound(Exception):
    pass


def create_patient(db: Session, data: PatientCreate) -> Patient:
    patient = Patient(**data.model_dump())
    db.add(patient)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(patient)
    return patient


def get_patient(db: Session, patient_id: uuid.UUID) -> Patient:
    patient = db.get(Patient, patient_id)
    if patient is None or patient.deleted_at is not None:
        raise PatientNotFound(str(patient_id))
    return patient


def list_patients(
    db: Session,
    *,
    last_name: str | None = None,
    date_of_birth: date | None = None,
    phone_number: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[Patient], int]:
    conditions = [Patient.deleted_at.is_(None)]
    if last_name:
        conditions.append(func.lower(Patient.last_name) == last_name.lower())
    if date_of_birth:
        conditions.append(Patient.date_of_birth == date_of_birth)
    if phone_number:
        conditions.append(Patient.phone_number == phone_number)
    total = db.scalar(select(func.count()).select_from(Patient).where(*conditions)) or 0
    rows = db.scalars(
        select(Patient).where(*conditions).order_by(Patient.created_at.desc()).limit(limit).offset(offset)
    ).all()
    return list(rows), total


def find_by_phone(db: Session, phone_number: str) -> Patient | None:
    """Most recently registered active patient with this phone number (duplicate detection)."""
    return db.scalars(
        select(Patient)
        .where(Patient.phone_number == phone_number, Patient.deleted_at.is_(None))
        .order_by(Patient.created_at.desc())
        .limit(1)
    ).first()


def update_patient(db: Session, patient_id: uuid.UUID, data: PatientUpdate) -> Patient:
    patient = get_patient(db, patient_id)
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(patient, field, value)
    patient.updated_at = utcnow()
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(patient)
    return patient


def soft_delete_patient(db: Session, patient_id: uuid.UUID) -> Patient:
    patient = get_patient(db, patient_id)
    patient.deleted_at = utcnow()
    patient.updated_at = patient.deleted_at
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(patient)
    return patient
