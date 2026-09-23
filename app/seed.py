"""Optional demo data (SEED_DEMO_DATA=true). Fictional people only - never real patient data."""
import logging
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Patient

log = logging.getLogger("app.seed")

DEMO_PATIENTS = [
    dict(first_name="Jane", last_name="Doe", date_of_birth=date(1985, 4, 12), sex="Female",
         phone_number="5550100123", email="jane.doe@example.com", address_line_1="123 Main Street",
         address_line_2="Apt 4B", city="Springfield", state="IL", zip_code="62701",
         insurance_provider="Blue Cross Blue Shield", insurance_member_id="XYZ123456789",
         preferred_language="English", emergency_contact_name="John Doe", emergency_contact_phone="5550100124"),
    dict(first_name="Carlos", last_name="Rivera", date_of_birth=date(1972, 11, 3), sex="Male",
         phone_number="5550100200", address_line_1="88 Oak Avenue", city="Austin", state="TX",
         zip_code="78701", preferred_language="Spanish"),
]


def seed_demo_data(db: Session) -> None:
    if db.scalar(select(func.count()).select_from(Patient)):
        return
    db.add_all(Patient(**p) for p in DEMO_PATIENTS)
    db.commit()
    log.info("seeded demo patients", extra={"data": {"count": len(DEMO_PATIENTS)}})
