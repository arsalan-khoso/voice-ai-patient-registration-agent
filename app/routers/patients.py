"""REST API: /patients. Thin HTTP layer - all logic lives in app.services.patients."""
import logging
import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app import validation as v
from app.db import get_db
from app.errors import APIError, envelope
from app.schemas import CallLogOut, PatientCreate, PatientOut, PatientUpdate
from app.security import require_api_key
from app.services import calls, patients

log = logging.getLogger("app.api")
router = APIRouter(prefix="/patients", tags=["patients"], dependencies=[Depends(require_api_key)])


def _out(patient) -> dict:
    return PatientOut.model_validate(patient).model_dump(mode="json")


@router.get("")
def list_patients(
    response: Response,
    last_name: str | None = Query(default=None, max_length=50),
    date_of_birth: str | None = Query(default=None, description="MM/DD/YYYY or YYYY-MM-DD"),
    phone_number: str | None = Query(default=None, description="Any format; normalised to 10 digits"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    dob: date | None = None
    phone: str | None = None
    try:
        if date_of_birth:
            dob = v.parse_date_of_birth(date_of_birth)
        if phone_number:
            phone = v.normalize_phone(phone_number)
    except ValueError as exc:
        raise APIError(400, "bad_request", str(exc))
    rows, total = patients.list_patients(
        db, last_name=(v.clean_text(last_name) if last_name else None),
        date_of_birth=dob, phone_number=phone, limit=limit, offset=offset,
    )
    response.headers["X-Total-Count"] = str(total)
    return envelope(data=[_out(p) for p in rows])


@router.get("/{patient_id}")
def get_patient(patient_id: uuid.UUID, db: Session = Depends(get_db)):
    return envelope(data=_out(patients.get_patient(db, patient_id)))


@router.post("", status_code=201)
def create_patient(payload: PatientCreate, db: Session = Depends(get_db)):
    patient = patients.create_patient(db, payload)
    log.info("patient created via API", extra={"data": {"patient_id": str(patient.patient_id)}})
    return envelope(data=_out(patient))


@router.put("/{patient_id}")
def update_patient(patient_id: uuid.UUID, payload: PatientUpdate, db: Session = Depends(get_db)):
    patient = patients.update_patient(db, patient_id, payload)
    return envelope(data=_out(patient))


@router.delete("/{patient_id}")
def delete_patient(patient_id: uuid.UUID, db: Session = Depends(get_db)):
    """Soft delete: sets deleted_at; the row is kept."""
    return envelope(data=_out(patients.soft_delete_patient(db, patient_id)))


@router.get("/{patient_id}/calls")
def patient_calls(patient_id: uuid.UUID, db: Session = Depends(get_db)):
    """Call transcripts/summaries linked to this patient (bonus feature)."""
    patients.get_patient(db, patient_id)
    rows = calls.list_calls_for_patient(db, patient_id)
    return envelope(data=[CallLogOut.model_validate(c).model_dump(mode="json") for c in rows])
