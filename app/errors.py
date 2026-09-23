"""Consistent { "data": ..., "error": ... } envelope + exception handlers."""
import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.schemas import format_error_dicts
from app.services.patients import PatientNotFound

log = logging.getLogger("app.errors")


def envelope(data=None, error=None) -> dict:
    return {"data": data, "error": error}


def error_response(status: int, code: str, message: str, details=None) -> JSONResponse:
    return JSONResponse(status_code=status, content=envelope(error={"code": code, "message": message, "details": details}))


class APIError(Exception):
    def __init__(self, status: int, code: str, message: str, details=None):
        self.status, self.code, self.message, self.details = status, code, message, details


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(APIError)
    async def _api_error(_: Request, exc: APIError):
        return error_response(exc.status, exc.code, exc.message, exc.details)

    @app.exception_handler(PatientNotFound)
    async def _not_found(_: Request, exc: PatientNotFound):
        return error_response(404, "not_found", "Patient not found.")

    @app.exception_handler(RequestValidationError)
    async def _request_validation(_: Request, exc: RequestValidationError):
        errors = exc.errors()
        if any(e["type"] == "json_invalid" for e in errors):
            return error_response(400, "malformed_json", "Request body is not valid JSON.")
        # path/query problems (bad UUID, bad limit) are 400; body field problems are 422
        if any(e["loc"] and e["loc"][0] in ("path", "query") for e in errors):
            fields = [str(e["loc"][-1]) for e in errors]
            return error_response(400, "bad_request", f"Invalid parameter(s): {', '.join(fields)}.")
        problems = format_error_dicts(errors)
        return error_response(422, "validation_error", "Request validation failed.", problems)

    @app.exception_handler(StarletteHTTPException)
    async def _http_exc(_: Request, exc: StarletteHTTPException):
        code = {401: "unauthorized", 404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return error_response(exc.status_code, code, str(exc.detail))

    @app.exception_handler(SQLAlchemyError)
    async def _db_error(_: Request, exc: SQLAlchemyError):
        log.error("database error", exc_info=exc)
        return error_response(500, "database_error", "A database error occurred. Please try again.")

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):
        log.error("unhandled error", exc_info=exc)
        return error_response(500, "internal_error", "An unexpected error occurred.")
