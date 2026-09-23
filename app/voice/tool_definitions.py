"""Function-tool schemas exposed to the LLM (OpenAI-style JSON schema, which Vapi accepts).
The provisioning script (voice_agent/provision_vapi.py) attaches these to the assistant and
points them at POST /vapi/webhook. Handlers live in app/voice/handlers.py - keep names in sync."""

_PATIENT_PROPERTIES = {
    "first_name": {"type": "string", "description": "Patient's first name"},
    "last_name": {"type": "string", "description": "Patient's last name"},
    "date_of_birth": {"type": "string", "description": "MM/DD/YYYY"},
    "sex": {"type": "string", "enum": ["Male", "Female", "Other", "Decline to Answer"]},
    "phone_number": {"type": "string", "description": "10-digit U.S. phone number, digits only"},
    "email": {"type": "string", "description": "Email address, optional"},
    "address_line_1": {"type": "string", "description": "Street number and name"},
    "address_line_2": {"type": "string", "description": "Apartment / suite / unit, optional"},
    "city": {"type": "string"},
    "state": {"type": "string", "description": "2-letter U.S. state abbreviation"},
    "zip_code": {"type": "string", "description": "5-digit ZIP or ZIP+4"},
    "insurance_provider": {"type": "string", "description": "Insurance company name, optional"},
    "insurance_member_id": {"type": "string", "description": "Member/subscriber ID, optional"},
    "preferred_language": {"type": "string", "description": "Defaults to English"},
    "emergency_contact_name": {"type": "string", "description": "Optional"},
    "emergency_contact_phone": {"type": "string", "description": "10-digit U.S. phone number, optional"},
}

TOOLS = [
    {
        "name": "validate_field",
        "description": (
            "Check ONE value the caller just gave you and get it back in normalized form. Call this "
            "immediately after the caller answers each of: date_of_birth, phone_number, email, state, "
            "zip_code, emergency_contact_phone. (Not needed for names, sex, city or street.) If valid=false, read the 'problem' to the caller in "
            "your own words and ask for that one field again. Do not narrate the tool call."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "field": {"type": "string", "enum": list(_PATIENT_PROPERTIES)},
                "value": {"type": "string", "description": "The value exactly as understood from the caller"},
            },
            "required": ["field", "value"],
        },
    },
    {
        "name": "lookup_patient_by_phone",
        "description": (
            "Check whether a patient with this phone number is already registered. Call it right after "
            "you have validated the caller's phone number, before collecting the address."
        ),
        "parameters": {
            "type": "object",
            "properties": {"phone_number": {"type": "string"}},
            "required": ["phone_number"],
        },
    },
    {
        "name": "save_patient",
        "description": (
            "Create the patient record. ONLY call this after you have read ALL collected information "
            "back to the caller and they clearly confirmed it is correct (set confirmed=true). "
            "Returns success or a list of field errors / a system error."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                **_PATIENT_PROPERTIES,
                "confirmed": {"type": "boolean", "description": "True only if the caller confirmed the read-back"},
                "allow_duplicate_phone": {
                    "type": "boolean",
                    "description": "True only if the caller said this is a different person who shares a phone number",
                },
            },
            "required": [
                "first_name", "last_name", "date_of_birth", "sex", "phone_number",
                "address_line_1", "city", "state", "zip_code", "confirmed",
            ],
        },
    },
    {
        "name": "update_patient",
        "description": (
            "Update an EXISTING patient (found via lookup_patient_by_phone) after the caller agreed to "
            "update their information and confirmed the changes. Pass only the fields that changed. "
            "date_of_birth is required as identity verification."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "patient_id": {"type": "string", "description": "patient_id returned by lookup_patient_by_phone"},
                **_PATIENT_PROPERTIES,
                "confirmed": {"type": "boolean"},
            },
            "required": ["patient_id", "date_of_birth", "confirmed"],
        },
    },
]
