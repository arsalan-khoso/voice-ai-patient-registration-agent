"""Pydantic request/response models. Validation lives here (single source of truth) and is
reused by the voice-agent tools, so the phone channel can never write data the API would reject."""
import uuid
from datetime import date, datetime, timezone

from pydantic import BaseModel, ConfigDict, ValidationError, field_serializer, field_validator, model_validator

from app import validation as v

FIELD_LABELS = {
    "first_name": "first name", "last_name": "last name", "date_of_birth": "date of birth",
    "sex": "sex", "phone_number": "phone number", "email": "email address",
    "address_line_1": "street address", "address_line_2": "apartment or suite", "city": "city",
    "state": "state", "zip_code": "ZIP code", "insurance_provider": "insurance provider",
    "insurance_member_id": "insurance member ID", "preferred_language": "preferred language",
    "emergency_contact_name": "emergency contact name", "emergency_contact_phone": "emergency contact phone number",
}
REQUIRED_FIELDS = (
    "first_name", "last_name", "date_of_birth", "sex", "phone_number",
    "address_line_1", "city", "state", "zip_code",
)
PATIENT_FIELDS = tuple(FIELD_LABELS)


class _PatientValidators(BaseModel):
    """Field validators only; the concrete fields live on the Create/Update subclasses."""
    model_config = ConfigDict(extra="forbid")

    @field_validator("first_name", "last_name", mode="before", check_fields=False)
    @classmethod
    def _names(cls, val, info):
        return v.validate_name(val, FIELD_LABELS[info.field_name])

    @field_validator("date_of_birth", mode="before", check_fields=False)
    @classmethod
    def _dob(cls, val):
        return v.parse_date_of_birth(val)

    @field_validator("sex", mode="before", check_fields=False)
    @classmethod
    def _sex(cls, val):
        return v.normalize_sex(val)

    @field_validator("phone_number", mode="before", check_fields=False)
    @classmethod
    def _phone(cls, val):
        return v.normalize_phone(val, "phone number", required=True)

    @field_validator("emergency_contact_phone", mode="before", check_fields=False)
    @classmethod
    def _ec_phone(cls, val):
        return v.normalize_phone(val, "emergency contact phone number", required=False)

    @field_validator("email", mode="before", check_fields=False)
    @classmethod
    def _email(cls, val):
        return v.normalize_email(val)

    @field_validator("address_line_1", mode="before", check_fields=False)
    @classmethod
    def _addr1(cls, val):
        return v.validate_text(val, "street address", max_len=200, required=True)

    @field_validator("address_line_2", mode="before", check_fields=False)
    @classmethod
    def _addr2(cls, val):
        return v.validate_text(val, "apartment or suite", max_len=100)

    @field_validator("city", mode="before", check_fields=False)
    @classmethod
    def _city(cls, val):
        return v.validate_text(val, "city", max_len=100, required=True)

    @field_validator("state", mode="before", check_fields=False)
    @classmethod
    def _state(cls, val):
        return v.normalize_state(val)

    @field_validator("zip_code", mode="before", check_fields=False)
    @classmethod
    def _zip(cls, val):
        return v.normalize_zip(val)

    @field_validator("insurance_provider", mode="before", check_fields=False)
    @classmethod
    def _ins_provider(cls, val):
        return v.validate_text(val, "insurance provider", max_len=100)

    @field_validator("insurance_member_id", mode="before", check_fields=False)
    @classmethod
    def _ins_member(cls, val):
        return v.normalize_member_id(val)

    @field_validator("preferred_language", mode="before", check_fields=False)
    @classmethod
    def _language(cls, val):
        return v.normalize_language(val)

    @field_validator("emergency_contact_name", mode="before", check_fields=False)
    @classmethod
    def _ec_name(cls, val):
        return v.validate_text(val, "emergency contact name", max_len=100)


class PatientCreate(_PatientValidators):
    first_name: str
    last_name: str
    date_of_birth: date
    sex: str
    phone_number: str
    email: str | None = None
    address_line_1: str
    address_line_2: str | None = None
    city: str
    state: str
    zip_code: str
    insurance_provider: str | None = None
    insurance_member_id: str | None = None
    preferred_language: str = "English"
    emergency_contact_name: str | None = None
    emergency_contact_phone: str | None = None


class PatientUpdate(_PatientValidators):
    """Partial update: only supplied fields change. Optional fields may be set to null to clear them."""
    first_name: str | None = None
    last_name: str | None = None
    date_of_birth: date | None = None
    sex: str | None = None
    phone_number: str | None = None
    email: str | None = None
    address_line_1: str | None = None
    address_line_2: str | None = None
    city: str | None = None
    state: str | None = None
    zip_code: str | None = None
    insurance_provider: str | None = None
    insurance_member_id: str | None = None
    preferred_language: str | None = None
    emergency_contact_name: str | None = None
    emergency_contact_phone: str | None = None

    @model_validator(mode="after")
    def _at_least_one_field(self):
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to update.")
        return self


class PatientOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    patient_id: uuid.UUID
    first_name: str
    last_name: str
    date_of_birth: date
    sex: str
    phone_number: str
    email: str | None
    address_line_1: str
    address_line_2: str | None
    city: str
    state: str
    zip_code: str
    insurance_provider: str | None
    insurance_member_id: str | None
    preferred_language: str
    emergency_contact_name: str | None
    emergency_contact_phone: str | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None

    @field_serializer("date_of_birth")
    def _ser_dob(self, d: date) -> str:
        return d.strftime("%m/%d/%Y")  # MM/DD/YYYY per the data model

    @field_serializer("created_at", "updated_at", "deleted_at")
    def _ser_ts(self, ts: datetime | None) -> str | None:
        if ts is None:
            return None
        return ts.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class CallLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    vapi_call_id: str
    patient_id: uuid.UUID | None
    caller_number: str | None
    started_at: datetime | None
    ended_at: datetime | None
    duration_seconds: int | None
    ended_reason: str | None
    summary: str | None
    transcript: str | None


def format_validation_errors(exc: ValidationError) -> list[dict]:
    return format_error_dicts(exc.errors())


def format_error_dicts(errors: list[dict]) -> list[dict]:
    """Turn Pydantic/FastAPI error dicts into [{field, message}] with human/voice-friendly messages."""
    problems = []
    for err in errors:
        loc = [str(p) for p in err["loc"] if p != "body"]
        field = loc[0] if loc else None
        label = FIELD_LABELS.get(field or "", field or "request")
        if err["type"] == "missing":
            message = f"The {label} is required."
        elif err["type"] == "extra_forbidden":
            message = f"'{field}' is not a field that can be set."
        elif err["type"] == "value_error":
            message = str(err["msg"]).removeprefix("Value error, ")
        else:
            message = f"The {label} is invalid."
        problems.append({"field": field, "message": message})
    return problems


def validate_single_field(field: str, value) -> tuple[bool, object, str | None]:
    """Validate one field in isolation (used by the voice agent's validate_field tool).
    Returns (ok, normalized_value_or_None, error_message_or_None)."""
    if field not in PATIENT_FIELDS:
        return False, None, f"Unknown field '{field}'."
    try:
        parsed = PatientUpdate(**{field: value})
    except ValidationError as exc:
        return False, None, "; ".join(p["message"] for p in format_validation_errors(exc))
    normalized = getattr(parsed, field)
    if isinstance(normalized, date):
        normalized = normalized.strftime("%m/%d/%Y")
    return True, normalized, None
