"""Create / update the Vapi assistant (and optionally buy a phone number) from code, so the
telephony setup is reproducible and reviewable instead of hand-clicked in a dashboard.

Usage (from the repo root, with the venv active):
    python -m voice_agent.provision_vapi                      # create or update the assistant
    python -m voice_agent.provision_vapi --buy-number 415     # ...and attach a free Vapi US number (area code 415)
    python -m voice_agent.provision_vapi --attach-number <phoneNumberId>

Environment:
    VAPI_API_KEY           Vapi private API key (dashboard -> API Keys)
    PUBLIC_BASE_URL        Public https URL of the deployed backend, e.g. https://voice-agent.onrender.com
    VAPI_WEBHOOK_SECRET    Same secret the backend was deployed with
    VAPI_ASSISTANT_ID      (optional) set to update an existing assistant instead of creating one
    LLM_PROVIDER / LLM_MODEL          default: openai / gpt-4.1
    VOICE_PROVIDER / VOICE_ID         default: vapi / Elliot (built-in, no extra vendor fee)
    TRANSCRIBER_MODEL / TRANSCRIBER_LANGUAGE   default: nova-3 / multi (auto-detects English/Spanish)
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

import httpx

from app.voice.tool_definitions import TOOLS

VAPI_API = "https://api.vapi.ai"
PROMPT_PATH = Path(__file__).with_name("system_prompt.md")
FIRST_MESSAGE = (
    "Thanks for calling Riverside Family Health, this is Sam. I can get you registered as a new patient "
    "in just a few minutes. Can I start with your first and last name?"
)


def env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None or value == "":
        sys.exit(f"Missing required environment variable: {name}")
    return value


def load_prompt() -> str:
    """The prompt file carries HTML design comments for reviewers; strip them so the LLM (and your token bill) never sees them."""
    return re.sub(r"<!--.*?-->\s*", "", PROMPT_PATH.read_text(encoding="utf-8"), flags=re.S).strip()


def build_assistant_payload() -> dict:
    base_url = env("PUBLIC_BASE_URL").rstrip("/")
    # Vapi's Server object has no "secret" field; auth is a custom header, which our backend checks.
    server = {
        "url": f"{base_url}/vapi/webhook",
        "headers": {"X-Vapi-Secret": env("VAPI_WEBHOOK_SECRET")},
        "timeoutSeconds": 20,
    }
    return {
        "name": "Riverside Patient Registration",
        "firstMessage": FIRST_MESSAGE,
        "model": {
            "provider": env("LLM_PROVIDER", "openai"),
            "model": env("LLM_MODEL", "gpt-4.1"),
            "temperature": 0.4,  # low: reliable tool use & data capture, still natural phrasing
            "messages": [{"role": "system", "content": load_prompt()}],
            "tools": [
                *[{"type": "function", "function": t, "server": server, "async": False} for t in TOOLS],
                {"type": "endCall"},
            ],
        },
        "voice": {"provider": env("VOICE_PROVIDER", "vapi"), "voiceId": env("VOICE_ID", "Elliot")},  # built-in voice: no extra per-minute vendor fee
        "transcriber": {
            "provider": "deepgram",
            "model": env("TRANSCRIBER_MODEL", "nova-3"),
            "language": env("TRANSCRIBER_LANGUAGE", "multi"),
        },
        # Where end-of-call reports (transcript, summary, drop reason) are delivered.
        "server": server,
        "serverMessages": ["end-of-call-report"],
        # Resilience / telephony behaviour
        "maxDurationSeconds": 900,  # hard cap so a stuck call can't run forever
        # Don't cut people off mid phone-number; handle interruptions quickly.
        "startSpeakingPlan": {"waitSeconds": 0.6, "smartEndpointingPlan": {"provider": "vapi"}},
        "stopSpeakingPlan": {"numWords": 2, "voiceSeconds": 0.2},
        # Dead line / caller went silent: nudge once, then hang up cleanly.
        "hooks": [
            {
                "on": "customer.speech.timeout",
                "options": {"timeoutSeconds": 12, "triggerMaxCount": 2, "triggerResetMode": "onUserSpeech"},
                "do": [{"type": "say", "exact": "Are you still there?"}],
            },
            {
                "on": "customer.speech.timeout",
                "options": {"timeoutSeconds": 40, "triggerMaxCount": 1, "triggerResetMode": "onUserSpeech"},
                "do": [
                    {"type": "say", "exact": "I'm not hearing anything, so I'll end the call. Please call back any time to register."},
                    {"type": "tool", "tool": {"type": "endCall"}},
                ],
            },
        ],
        "analysisPlan": {
            "summaryPlan": {
                "enabled": True,
                "messages": [
                    {"role": "system", "content": "Summarize this call in two sentences: who called, whether registration was "
                                                   "completed or an existing record updated, and any problems. Return only the summary."},
                    {"role": "user", "content": "Transcript:\n\n{{transcript}}\n\nEnded reason: {{endedReason}}"},
                ],
            }
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--buy-number", metavar="AREA_CODE", help="Create a free Vapi U.S. number in this area code")
    parser.add_argument("--attach-number", metavar="PHONE_NUMBER_ID", help="Attach an existing Vapi phone number")
    parser.add_argument("--dry-run", action="store_true", help="Print the assistant payload and exit")
    args = parser.parse_args()

    payload = build_assistant_payload()
    if args.dry_run:
        print(json.dumps(payload, indent=2))
        return

    headers = {"Authorization": f"Bearer {env('VAPI_API_KEY')}", "Content-Type": "application/json"}
    with httpx.Client(base_url=VAPI_API, headers=headers, timeout=30) as api:
        assistant_id = os.environ.get("VAPI_ASSISTANT_ID")
        res = api.patch(f"/assistant/{assistant_id}", json=payload) if assistant_id else api.post("/assistant", json=payload)
        res.raise_for_status()
        assistant_id = res.json()["id"]
        print(f"Assistant ready: {assistant_id}  (set VAPI_ASSISTANT_ID={assistant_id} to update it next time)")

        if args.buy_number:
            res = api.post("/phone-number", json={
                "provider": "vapi", "numberDesiredAreaCode": args.buy_number,
                "assistantId": assistant_id, "name": "Patient registration line",
            })
            if res.status_code >= 400:
                sys.exit(f"Vapi refused the number request: {res.text}  (try another --buy-number area code)")
            info = res.json()
            print(f"Phone number: {info.get('number') or info.get('id')}  (id {info['id']})")
        elif args.attach_number:
            res = api.patch(f"/phone-number/{args.attach_number}", json={"assistantId": assistant_id})
            res.raise_for_status()
            print(f"Attached assistant to phone number {res.json().get('number')}")


if __name__ == "__main__":
    main()
