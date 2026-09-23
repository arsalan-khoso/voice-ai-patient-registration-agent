"""REST API tests: CRUD, validation, status codes, envelope, soft delete, filters, auth."""
import uuid


def _assert_envelope(body):
    assert set(body) == {"data", "error"}


def test_create_returns_201_with_normalized_record(client, valid_patient):
    res = client.post("/patients", json=valid_patient)
    assert res.status_code == 201
    body = res.json()
    _assert_envelope(body)
    assert body["error"] is None
    data = body["data"]
    uuid.UUID(data["patient_id"])
    assert data["phone_number"] == "4155550134"      # normalised to 10 digits
    assert data["state"] == "IL"                      # normalised to uppercase
    assert data["date_of_birth"] == "04/12/1985"      # MM/DD/YYYY
    assert data["preferred_language"] == "English"    # default
    assert data["created_at"].endswith("Z") and data["deleted_at"] is None


def test_create_missing_required_fields_is_422(client):
    res = client.post("/patients", json={"first_name": "Jane"})
    assert res.status_code == 422
    body = res.json()
    assert body["data"] is None and body["error"]["code"] == "validation_error"
    fields = {d["field"] for d in body["error"]["details"]}
    assert {"last_name", "date_of_birth", "sex", "phone_number", "address_line_1", "city", "state", "zip_code"} <= fields


def test_future_date_of_birth_rejected(client, valid_patient):
    valid_patient["date_of_birth"] = "01/01/2999"
    res = client.post("/patients", json=valid_patient)
    assert res.status_code == 422
    assert res.json()["error"]["details"][0]["field"] == "date_of_birth"
    assert "future" in res.json()["error"]["details"][0]["message"]


def test_invalid_field_values_rejected(client, valid_patient):
    cases = {
        "phone_number": "123",
        "email": "not-an-email",
        "state": "ZZ",
        "zip_code": "1234",
        "sex": "robot",
        "first_name": "J4ne",
        "last_name": "<script>",
        "emergency_contact_phone": "55501",
    }
    for field, bad in cases.items():
        res = client.post("/patients", json={**valid_patient, field: bad})
        assert res.status_code == 422, field
        assert res.json()["error"]["details"][0]["field"] == field


def test_name_edge_cases_accepted(client, valid_patient):
    res = client.post("/patients", json={**valid_patient, "first_name": "Mary-Jane", "last_name": "O'Brien"})
    assert res.status_code == 201
    assert res.json()["data"]["last_name"] == "O'Brien"


def test_zip_plus_four_and_optional_fields(client, valid_patient):
    res = client.post("/patients", json={
        **valid_patient, "zip_code": "62701-1234", "email": "Jane@Example.com", "address_line_2": "Apt 4",
        "insurance_provider": "Aetna", "insurance_member_id": "ab-123", "emergency_contact_name": "John Doe",
        "emergency_contact_phone": "415-555-0199", "sex": "decline to answer",
    })
    assert res.status_code == 201
    data = res.json()["data"]
    assert data["zip_code"] == "62701-1234" and data["email"] == "jane@example.com"
    assert data["insurance_member_id"] == "AB-123" and data["sex"] == "Decline to Answer"


def test_unknown_and_server_managed_fields_rejected(client, valid_patient):
    res = client.post("/patients", json={**valid_patient, "patient_id": str(uuid.uuid4())})
    assert res.status_code == 422


def test_malformed_json_is_400(client):
    res = client.post("/patients", content="{not json", headers={"content-type": "application/json"})
    assert res.status_code == 400 and res.json()["error"]["code"] == "malformed_json"


def test_get_by_id_and_errors(client, valid_patient):
    created = client.post("/patients", json=valid_patient).json()["data"]
    ok = client.get(f"/patients/{created['patient_id']}")
    assert ok.status_code == 200 and ok.json()["data"]["patient_id"] == created["patient_id"]
    assert client.get(f"/patients/{uuid.uuid4()}").status_code == 404
    bad = client.get("/patients/not-a-uuid")
    assert bad.status_code == 400 and bad.json()["data"] is None


def test_list_and_filters(client, valid_patient):
    client.post("/patients", json=valid_patient)
    client.post("/patients", json={**valid_patient, "first_name": "Bob", "last_name": "Smith",
                                   "date_of_birth": "1/2/1970", "phone_number": "2125550111"})
    assert len(client.get("/patients").json()["data"]) == 2
    assert client.get("/patients").headers["X-Total-Count"] == "2"

    by_last = client.get("/patients", params={"last_name": "smith"}).json()["data"]
    assert [p["first_name"] for p in by_last] == ["Bob"]
    by_dob = client.get("/patients", params={"date_of_birth": "04/12/1985"}).json()["data"]
    assert [p["last_name"] for p in by_dob] == ["Doe"]
    by_phone = client.get("/patients", params={"phone_number": "(212) 555-0111"}).json()["data"]
    assert [p["last_name"] for p in by_phone] == ["Smith"]
    assert client.get("/patients", params={"last_name": "Nobody"}).json()["data"] == []


def test_list_bad_query_params_400(client):
    assert client.get("/patients", params={"date_of_birth": "garbage"}).status_code == 400
    assert client.get("/patients", params={"phone_number": "12"}).status_code == 400
    assert client.get("/patients", params={"limit": 0}).status_code == 400


def test_partial_update(client, valid_patient):
    pid = client.post("/patients", json=valid_patient).json()["data"]["patient_id"]
    before = client.get(f"/patients/{pid}").json()["data"]
    res = client.put(f"/patients/{pid}", json={"city": "Chicago", "email": "new@example.com"})
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["city"] == "Chicago" and data["email"] == "new@example.com"
    assert data["first_name"] == "Jane"                      # untouched
    assert data["updated_at"] >= before["updated_at"] and data["created_at"] == before["created_at"]


def test_update_validation_and_not_found(client, valid_patient):
    pid = client.post("/patients", json=valid_patient).json()["data"]["patient_id"]
    assert client.put(f"/patients/{pid}", json={"zip_code": "abc"}).status_code == 422
    assert client.put(f"/patients/{pid}", json={}).status_code == 422                       # nothing to update
    assert client.put(f"/patients/{pid}", json={"first_name": None}).status_code == 422     # required can't be nulled
    assert client.put(f"/patients/{uuid.uuid4()}", json={"city": "X"}).status_code == 404


def test_update_can_clear_optional_field(client, valid_patient):
    pid = client.post("/patients", json={**valid_patient, "email": "a@b.com"}).json()["data"]["patient_id"]
    res = client.put(f"/patients/{pid}", json={"email": None})
    assert res.status_code == 200 and res.json()["data"]["email"] is None


def test_soft_delete(client, valid_patient):
    pid = client.post("/patients", json=valid_patient).json()["data"]["patient_id"]
    res = client.delete(f"/patients/{pid}")
    assert res.status_code == 200 and res.json()["data"]["deleted_at"] is not None
    assert client.get(f"/patients/{pid}").status_code == 404
    assert client.get("/patients").json()["data"] == []
    assert client.delete(f"/patients/{pid}").status_code == 404
    assert client.put(f"/patients/{pid}", json={"city": "X"}).status_code == 404


def test_soft_deleted_row_is_kept_in_database(client, session_factory, valid_patient):
    from app.models import Patient
    pid = client.post("/patients", json=valid_patient).json()["data"]["patient_id"]
    client.delete(f"/patients/{pid}")
    with session_factory() as db:
        row = db.get(Patient, uuid.UUID(pid))
        assert row is not None and row.deleted_at is not None


def test_unknown_route_uses_envelope(client):
    res = client.get("/nope")
    assert res.status_code == 404 and res.json()["error"]["code"] == "not_found"


def test_api_key_enforced_when_configured(client, valid_patient, settings_override):
    settings_override(api_key="s3cret")
    assert client.get("/patients").status_code == 401
    assert client.get("/patients", headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.get("/patients", headers={"X-API-Key": "s3cret"}).status_code == 200
    assert client.get("/health").status_code == 200  # health stays open for platform probes


def test_health(client):
    res = client.get("/health")
    assert res.status_code == 200 and res.json()["data"]["status"] == "ok"


def test_database_failure_returns_500_envelope(client, valid_patient, monkeypatch):
    from app.services import patients as svc

    def boom(*a, **k):
        from sqlalchemy.exc import OperationalError
        raise OperationalError("insert", {}, Exception("db down"))

    monkeypatch.setattr(svc, "create_patient", boom)
    res = client.post("/patients", json=valid_patient)
    assert res.status_code == 500
    assert res.json()["data"] is None and res.json()["error"]["code"] == "database_error"
