"""Vapi webhook (the telephony <-> backend boundary).

Vapi runs STT, the LLM and TTS; we only receive server messages:
  * tool-calls          -> run the requested tool(s), reply {"results": [{toolCallId, result}]}
  * end-of-call-report  -> store transcript/summary/end reason (also fires when the caller drops mid-call)
  * everything else     -> acknowledged and ignored
Docs: https://docs.vapi.ai/server-url/events
"""
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool

from app.db import SessionLocal
from app.errors import APIError
from app.security import require_vapi_secret
from app.services import calls
from app.voice.handlers import run_tool

log = logging.getLogger("app.vapi")
router = APIRouter(prefix="/vapi", tags=["vapi"], dependencies=[Depends(require_vapi_secret)])


def _parse_ts(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _call_context(message: dict) -> dict:
    call = message.get("call") or {}
    customer = call.get("customer") or message.get("customer") or {}
    return {"call_id": call.get("id"), "caller_number": customer.get("number")}


def _extract_tool_calls(message: dict) -> list[tuple[str, str, object]]:
    """Return [(tool_call_id, name, arguments)] handling both current and legacy Vapi payload shapes."""
    out = []
    for item in message.get("toolCallList") or []:
        fn = item.get("function") or {}
        arguments = item.get("arguments")
        if arguments is None:
            arguments = fn.get("arguments", item.get("parameters"))
        out.append((item.get("id"), item.get("name") or fn.get("name"), arguments))
    if not out:
        for item in message.get("toolCalls") or []:
            fn = item.get("function") or {}
            out.append((item.get("id"), fn.get("name"), fn.get("arguments")))
    return out


def _handle_tool_calls(message: dict) -> dict:
    ctx = _call_context(message)
    results = []
    with SessionLocal() as db:
        for tool_call_id, name, arguments in _extract_tool_calls(message):
            log.info("tool call", extra={"data": {"tool": name, "call_id": ctx["call_id"]}})
            results.append({"name": name, "toolCallId": tool_call_id, "result": run_tool(db, name, arguments, ctx)})
    return {"results": results}


def _handle_end_of_call(message: dict) -> dict:
    ctx = _call_context(message)
    if not ctx["call_id"]:
        return {}
    artifact = message.get("artifact") or {}
    analysis = message.get("analysis") or {}
    transcript = artifact.get("transcript") or message.get("transcript")
    summary = analysis.get("summary") or message.get("summary")
    started_at, ended_at = _parse_ts(message.get("startedAt")), _parse_ts(message.get("endedAt"))
    # Vapi's end-of-call-report carries no duration field; derive it from the timestamps.
    duration = int((ended_at - started_at).total_seconds()) if started_at and ended_at else None
    with SessionLocal() as db:
        record = calls.record_call_report(
            db, ctx["call_id"],
            caller_number=ctx["caller_number"],
            started_at=started_at,
            ended_at=ended_at,
            duration_seconds=duration,
            ended_reason=message.get("endedReason"),
            summary=summary,
            transcript=transcript,
        )
        registered = record.patient_id is not None
    log.info(
        "call ended",
        extra={"data": {"call_id": ctx["call_id"], "ended_reason": message.get("endedReason"),
                        "registered": registered, "duration_s": duration}},
    )
    return {}


@router.post("/webhook")
async def webhook(request: Request):
    try:
        body = await request.json()
    except ValueError:
        raise APIError(400, "malformed_json", "Request body is not valid JSON.")
    message = body.get("message") if isinstance(body, dict) else None
    if not isinstance(message, dict):
        raise APIError(400, "bad_request", "Expected a Vapi server message.")

    kind = message.get("type")
    if kind == "tool-calls":
        return await run_in_threadpool(_handle_tool_calls, message)
    if kind == "end-of-call-report":
        return await run_in_threadpool(_handle_end_of_call, message)
    return {}
