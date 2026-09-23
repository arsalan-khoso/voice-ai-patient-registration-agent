"""Voice-agent backend tests: simulate the Vapi webhook exactly as Vapi would call it."""
import json

CALL = {"id": "call-1", "customer": {"number": "+14155550134"}}


def tool_call(client, name, arguments, call=CALL, headers=None):
    body = {"message": {"type": "tool-calls", "call": call,
                        "toolCallList": [{"id": "tc-1", "name": name, "arguments": arguments}]}}
    res = client.post("/vapi/webhook", json=body, headers=headers or {})
    assert res.status_code == 200, res.text
    result = res.json()["results"][0]
    assert result["toolCallId"] == "tc-1"
    return json.loads(result["result"])


def full_args(valid_patient, **extra):
    return {**valid_patient, "confirmed": True, **extra}


def test_validate_field_ok_and_specific_errors(client):
    ok = tool_call(client, "validate_field", {"field": "date_of_birth", "value": "4/12/1985"})
    assert ok == {"valid": True, "normalized": "04/12/1985"}

    future = tool_call(client, "validate_field", {"field": "date_of_birth", "value": "01/01/2999"})
    assert future["valid"] is False and "future" in future["problem"]

    short_phone = tool_call(client, "validate_field", {"field": "phone_number", "value": "555"})
    assert short_phone["valid"] is False and "10 digits" in short_phone["problem"]

    state = tool_call(client, "validate_field", {"field": "state", "value": "California"})
    assert state == {"valid": True, "normalized": "CA"}


def test_arguments_may_arrive_as_json_string(client):
    out = tool_call(client, "validate_field", json.dumps({"field": "zip_code", "value": "62701"}))
    assert out["valid"] is True


def test_legacy_tool_call_shape(client):
    body = {"message": {"type": "tool-calls", "call": CALL, "toolCalls": [
        {"id": "x", "function": {"name": "validate_field", "arguments": {"field": "sex", "value": "female"}}}]}}
    res = client.post("/vapi/webhook", json=body)
    assert json.loads(res.json()["results"][0]["result"])["normalized"] == "Female"


def test_save_patient_persists_and_is_retrievable_via_api(client, valid_patient):
    out = tool_call(client, "save_patient", full_args(valid_patient))
    assert out["success"] is True and out["first_name"] == "Jane"
    listed = client.get("/patients", params={"last_name": "Doe"}).json()["data"]
    assert len(listed) == 1 and listed[0]["patient_id"] == out["patient_id"]


def test_save_requires_confirmation(client, valid_patient):
    out = tool_call(client, "save_patient", {**valid_patient, "confirmed": False})
    assert out["success"] is False
    assert client.get("/patients").json()["data"] == []


def test_save_invalid_data_returns_field_errors(client, valid_patient):
    out = tool_call(client, "save_patient", full_args(valid_patient, date_of_birth="12/31/2999"))
    assert out["success"] is False
    assert out["errors"][0]["field"] == "date_of_birth"
    assert client.get("/patients").json()["data"] == []


def test_save_ignores_blank_optional_fields_from_llm(client, valid_patient):
    out = tool_call(client, "save_patient", full_args(valid_patient, email="", address_line_2=None, insurance_provider=""))
    assert out["success"] is True


def test_save_is_idempotent_within_a_call(client, valid_patient):
    first = tool_call(client, "save_patient", full_args(valid_patient))
    second = tool_call(client, "save_patient", full_args(valid_patient))
    assert second["success"] is True and second["patient_id"] == first["patient_id"]
    assert len(client.get("/patients").json()["data"]) == 1


def test_duplicate_phone_detected_across_calls(client, valid_patient):
    tool_call(client, "save_patient", full_args(valid_patient))
    lookup = tool_call(client, "lookup_patient_by_phone", {"phone_number": "415-555-0134"}, call={"id": "call-2"})
    assert lookup["found"] is True and lookup["first_name"] == "Jane" and lookup["last_name"] == "Doe"
    assert tool_call(client, "lookup_patient_by_phone", {"phone_number": "2125550000"}, call={"id": "call-2"}) == {"found": False}

    dup = tool_call(client, "save_patient", full_args(valid_patient), call={"id": "call-2"})
    assert dup["success"] is False and dup["duplicate"] is True
    allowed = tool_call(client, "save_patient", full_args(valid_patient, first_name="Sam", allow_duplicate_phone=True),
                        call={"id": "call-3"})
    assert allowed["success"] is True


def test_update_existing_patient_requires_matching_dob(client, valid_patient):
    saved = tool_call(client, "save_patient", full_args(valid_patient))
    pid = saved["patient_id"]

    wrong = tool_call(client, "update_patient", {"patient_id": pid, "date_of_birth": "01/01/1990", "city": "Chicago",
                                                 "confirmed": True}, call={"id": "call-2"})
    assert wrong["success"] is False and wrong["verification_failed"] is True

    right = tool_call(client, "update_patient", {"patient_id": pid, "date_of_birth": "04/12/1985", "city": "Chicago",
                                                 "email": "jane@example.com", "confirmed": True}, call={"id": "call-2"})
    assert right["success"] is True
    record = client.get(f"/patients/{pid}").json()["data"]
    assert record["city"] == "Chicago" and record["email"] == "jane@example.com" and record["first_name"] == "Jane"


def test_database_failure_during_save_is_reported_not_silent(client, valid_patient, monkeypatch):
    from sqlalchemy.exc import OperationalError
    from app.services import patients as svc

    def boom(*a, **k):
        raise OperationalError("insert", {}, Exception("db down"))

    monkeypatch.setattr(svc, "create_patient", boom)
    out = tool_call(client, "save_patient", full_args(valid_patient))
    assert out["success"] is False and out["system_error"] is True and "instruction" in out


def test_unknown_tool_and_handler_crash_are_contained(client, monkeypatch):
    assert "error" in tool_call(client, "does_not_exist", {})
    from app.voice import handlers
    monkeypatch.setitem(handlers.HANDLERS, "validate_field", lambda *a: 1 / 0)
    assert tool_call(client, "validate_field", {"field": "city", "value": "x"})["system_error"] is True


def test_end_of_call_report_stores_transcript_linked_to_patient(client, valid_patient):
    saved = tool_call(client, "save_patient", full_args(valid_patient))
    report = {"message": {
        "type": "end-of-call-report", "call": CALL, "endedReason": "assistant-ended-call",
        "startedAt": "2026-01-01T10:00:00.000Z", "endedAt": "2026-01-01T10:03:10.000Z",
        "artifact": {"transcript": "AI: Hello...\nUser: Hi, I'm Jane Doe."},
        "analysis": {"summary": "Jane registered."},
    }}
    assert client.post("/vapi/webhook", json=report).status_code == 200
    calls = client.get(f"/patients/{saved['patient_id']}/calls").json()["data"]
    assert len(calls) == 1
    assert calls[0]["transcript"].startswith("AI: Hello") and calls[0]["duration_seconds"] == 190
    assert calls[0]["ended_reason"] == "assistant-ended-call"


def test_dropped_call_without_registration_is_recorded(client, session_factory):
    report = {"message": {"type": "end-of-call-report", "call": {"id": "call-drop"},
                          "endedReason": "customer-ended-call", "transcript": "AI: Hi"}}
    assert client.post("/vapi/webhook", json=report).status_code == 200
    from app.models import CallLog
    with session_factory() as db:
        row = db.query(CallLog).filter_by(vapi_call_id="call-drop").one()
        assert row.patient_id is None and row.ended_reason == "customer-ended-call"


def test_webhook_secret_enforced(client, settings_override):
    settings_override(vapi_webhook_secret="hook-secret")
    body = {"message": {"type": "status-update"}}
    assert client.post("/vapi/webhook", json=body).status_code == 401
    assert client.post("/vapi/webhook", json=body, headers={"x-vapi-secret": "nope"}).status_code == 401
    assert client.post("/vapi/webhook", json=body, headers={"x-vapi-secret": "hook-secret"}).status_code == 200


def test_webhook_rejects_garbage(client):
    assert client.post("/vapi/webhook", content="x", headers={"content-type": "application/json"}).status_code == 400
    assert client.post("/vapi/webhook", json={"nope": 1}).status_code == 400
