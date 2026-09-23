"""Operational endpoints: health check (for the platform's probe) and the read-only dashboard."""
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_db
from app.errors import envelope, error_response

router = APIRouter()
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@router.get("/health", tags=["ops"])
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        return error_response(503, "unhealthy", "Database is unreachable.")
    return envelope(data={"status": "ok"})


@router.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/dashboard")


@router.get("/dashboard", include_in_schema=False)
def dashboard():
    return FileResponse(STATIC_DIR / "dashboard.html")
