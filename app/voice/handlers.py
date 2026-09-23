"""Server-side implementations of the voice agent's tools.

Design rules:
  * Handlers never raise to the caller: every outcome is a small JSON dict the LLM can act on
    (so a DB failure becomes an apology + retry, never dead air).
  * They call the same service layer + Pydantic schemas as the REST API, so voice and HTTP
    can't diverge on validation.
"""
import json
import logging
import uuid
from typing import Any, Callable

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app import validation as v
from app.config import get_settings
from app.schemas import (
    PATIENT_FIELDS, PatientCreate, PatientUpdate, format_validation_errors, validate_single_field,
)
from app.services import calls, patients

log = logging.getLogger("app.voice")


def _safe_payload(payload: dict) -> dict:
    """Mask contact details / DOB in logs when LOG_PII=false."""
    if get_settings().log_pii:
        return payload
    masked = dict(payload)
    for key in ("phone_number", "emergency_contact_phone"):
        if masked.get(key):
            masked[key] = v.mask_phone(str(masked[key]))
    for key in ("email", "date_of_birth", "insurance_member_id", "address_line_1"):
        if masked.get(key):
            masked[key] = "***"
    return masked


def _patient_fields(args: dict) -> dict:
    """Keep only real patient fields; drop empty strings/nulls the LLM sometimes sends for skipped optionals."""
    return {k: val for k, val in args.items() if k in PATIENT_FIELDS and val not in (None, "")}


def validate_field(db: Session, args: dict, ctx: dict) -> dict:
    ok, normalized, problem = validate_single_field(str(args.get("field", "")), args.get("value"))
    if ok:
        return {"valid": True, "normalized": normalized}
    return {"valid": False, "problem": problem}


def lookup_patient_by_phone(db: Session, args: dict, ctx: dict) -> dict:
    try:
        phone = v.normalize_phone(args.get("phone_number"))
    except ValueError as exc:
        return {"found": False, "valid": False, "problem": str(exc)}
    match = patients.find_by_phone(db, phone)
    if not match:
        return {"found": False}
    return {
        "found": True,
        "patient_id": str(match.patient_id),
        "first_name": match.first_name,
        "last_name": match.last_name,
    }


def save_patient(db: Session, args: dict, ctx: dict) -> dict:
    call_id = ctx.get("call_id")
    if not args.get("confirmed"):
        return {"success": False, "problem": "The caller has not confirmed the information yet. Read it all back first."}

    # Idempotency: the LLM (or Vapi) may retry; never create two records for one call.
    if call_id:
        already = calls.patient_for_call(db, call_id)
        if already:
            return {"success": True, "patient_id": str(already), "note": "Already saved during this call."}

    fields = _patient_fields(args)
    try:
        data = PatientCreate(**fields)
    except ValidationError as exc:
        errors = format_validation_errors(exc)
        log.info("save_patient rejected", extra={"data": {"call_id": call_id, "errors": errors}})
        return {
            "success": False,
            "errors": errors,
            "instruction": "Tell the caller which specific field(s) were a problem and ask for just those again.",
        }

    try:
        existing = patients.find_by_phone(db, data.phone_number)
        if existing and not args.get("allow_duplicate_phone"):
            return {
                "success": False,
                "duplicate": True,
                "patient_id": str(existing.patient_id),
                "first_name": existing.first_name,
                "last_name": existing.last_name,
                "instruction": "A record with this phone number exists. Ask if they want to update it instead.",
            }
        patient = patients.create_patient(db, data)
        if call_id:
            calls.link_patient(db, call_id, patient.patient_id, ctx.get("caller_number"))
    except SQLAlchemyError:
        db.rollback()
        log.exception("save_patient database failure", extra={"data": {"call_id": call_id}})
        # The payload is logged so nothing the caller told us is lost even when the write fails.
        log.error("unsaved patient payload", extra={"data": _safe_payload(fields)})
        return {
            "success": False,
            "system_error": True,
            "instruction": (
                "The system could not save right now. Apologise, offer to try once more, and if it "
                "fails again tell the caller to call back shortly. Never pretend it was saved."
            ),
        }

    log.info(
        "patient registered via voice",
        extra={"data": {"call_id": call_id, "patient_id": str(patient.patient_id),
                        "payload": _safe_payload(data.model_dump(mode="json"))}},
    )
    return {"success": True, "patient_id": str(patient.patient_id), "first_name": patient.first_name}


def update_patient(db: Session, args: dict, ctx: dict) -> dict:
    call_id = ctx.get("call_id")
    if not args.get("confirmed"):
        return {"success": False, "problem": "The caller has not confirmed the changes yet."}
    try:
        patient_id = uuid.UUID(str(args.get("patient_id", "")))
    except ValueError:
        return {"success": False, "problem": "Invalid patient_id; use the one from lookup_patient_by_phone."}

    try:
        claimed_dob = v.parse_date_of_birth(args.get("date_of_birth"))
    except ValueError as exc:
        return {"success": False, "errors": [{"field": "date_of_birth", "message": str(exc)}]}

    try:
        patient = patients.get_patient(db, patient_id)
        if patient.date_of_birth != claimed_dob:
            log.warning("update_patient identity check failed", extra={"data": {"call_id": call_id}})
            return {
                "success": False,
                "verification_failed": True,
                "instruction": "The date of birth doesn't match our record. Don't reveal the record; offer to re-check the date.",
            }
        changes = _patient_fields(args)
        changes.pop("date_of_birth", None)  # DOB was only for verification
        if not changes:
            return {"success": False, "problem": "No fields to change were provided."}
        updated = patients.update_patient(db, patient_id, PatientUpdate(**changes))
        if call_id:
            calls.link_patient(db, call_id, updated.patient_id, ctx.get("caller_number"))
    except patients.PatientNotFound:
        return {"success": False, "problem": "That patient record no longer exists."}
    except ValidationError as exc:
        return {"success": False, "errors": format_validation_errors(exc)}
    except SQLAlchemyError:
        db.rollback()
        log.exception("update_patient database failure", extra={"data": {"call_id": call_id}})
        return {"success": False, "system_error": True,
                "instruction": "The system could not save right now. Apologise and offer to retry once."}

    log.info("patient updated via voice",
             extra={"data": {"call_id": call_id, "patient_id": str(patient_id), "changed": sorted(changes)}})
    return {"success": True, "patient_id": str(patient_id), "first_name": updated.first_name}


HANDLERS: dict[str, Callable[[Session, dict, dict], dict]] = {
    "validate_field": validate_field,
    "lookup_patient_by_phone": lookup_patient_by_phone,
    "save_patient": save_patient,
    "update_patient": update_patient,
}


def run_tool(db: Session, name: str, arguments: Any, ctx: dict) -> str:
    """Dispatch one tool call and return the JSON string Vapi feeds back to the LLM."""
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments or "{}")
        except json.JSONDecodeError:
            arguments = {}
    handler = HANDLERS.get(name)
    if handler is None:
        return json.dumps({"error": f"Unknown tool '{name}'."})
    try:
        return json.dumps(handler(db, arguments or {}, ctx))
    except Exception:  # last-resort guard: the caller must never get silence
        db.rollback()
        log.exception("tool handler crashed", extra={"data": {"tool": name, "call_id": ctx.get("call_id")}})
        return json.dumps({"success": False, "system_error": True,
                           "instruction": "Something went wrong on our side. Apologise and offer to retry once."})
