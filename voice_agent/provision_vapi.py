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
    VOICE_PROVIDER / VOICE_ID         default: openai / marin (gpt-4o-mini-tts, steerable). Also: 11labs, cartesia, rime-ai, vapi (see build_voice)
    TRANSCRIBER_PROVIDER   default: soniox (stt-rt-v5, English+Spanish hints); "deepgram" also supported
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
    "Hi, thanks for calling Riverside Family Health, this is Sam. I can get you registered as a new patient, "
    "it only takes a few minutes. Can I start with your first and last name?"
)


def env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None or value == "":
        sys.exit(f"Missing required environment variable: {name}")
    return value


def load_prompt() -> str:
    """The prompt file carries HTML design comments for reviewers; strip them so the LLM (and your token bill) never sees them."""
    return re.sub(r"<!--.*?-->\s*", "", PROMPT_PATH.read_text(encoding="utf-8"), flags=re.S).strip()


# Domain terms the recogniser should favour (insurers are the hardest words on a registration call).
VOCABULARY = [
    "Blue Cross Blue Shield", "Aetna", "Cigna", "UnitedHealthcare", "Humana", "Kaiser Permanente", "Anthem",
    "Medicare", "Medicaid", "Tricare", "Riverside Family Health", "Arsalan", "Ahmed", "Muhammad", "Hussain", "Khan", "Ali", "Fatima", "Nguyen", "Garcia", "Rodriguez", "Patel",
]


def build_transcriber() -> dict:
    """Soniox handles accented English + names far better than the Deepgram multi-language mode we started with
    (first real call: Deepgram returned only "My name is." and dropped the name). Deepgram remains selectable."""
    if env("TRANSCRIBER_PROVIDER", "soniox") == "deepgram":
        return {"provider": "deepgram", "model": env("TRANSCRIBER_MODEL", "nova-3"), "language": env("TRANSCRIBER_LANGUAGE", "en")}
    return {
        "provider": "soniox",
        "model": "stt-rt-v5",
        "languages": ["en", "es"],       # bias to English, still understand Spanish ("Hablo espanol")
        "languageHintsStrict": False,
        "endpointSensitivity": -0.3,      # callers pause mid-sentence and while reading digits/spelling
        "maxEndpointDelayMs": 1000,
        "customVocabulary": VOCABULARY,
        "contextGeneral": [
            {"key": "domain", "value": "Healthcare patient registration phone call"},
            {"key": "topic", "value": "Caller gives name, spelling letter by letter, date of birth, phone number, US address, ZIP code, insurance"},
            {"key": "callers", "value": "US callers with many accents, including South Asian, Hispanic and Arabic names"},
        ],
    }


# Delivery direction for OpenAI's steerable TTS: this is what makes it sound like a person on a phone, not a reader.
VOICE_STYLE = (
    "Voice: a friendly American woman in her early thirties working the front desk of a small family clinic. "
    "Delivery: relaxed, warm and conversational, like chatting with a regular patient - never like an announcer, "
    "narrator or automated system. Let a light smile come through. "
    "Pacing: natural and unhurried but not slow; tiny pauses at commas, a slightly longer beat between thoughts. "
    "Intonation: gentle rise on questions, soft and reassuring when the caller hesitates, a little brighter on "
    "\"perfect\" and \"you're all set\". Vary pitch naturally; avoid a flat, even, read-aloud rhythm. "
    "Numbers: read phone numbers and ZIP codes in small, clear groups with short pauses. When reading a spelling, "
    "say each letter as its own distinct letter name with a tiny pause after it."
)


def build_voice() -> dict:
    """Default: OpenAI gpt-4o-mini-tts 'marin' - steerable, very natural. Alternatives via env, e.g.
    VOICE_PROVIDER=11labs VOICE_ID=sarah | cartesia (+VOICE_MODEL=sonic-3) | rime-ai (+VOICE_MODEL=arcana) | vapi VOICE_ID=Elliot."""
    provider = env("VOICE_PROVIDER", "openai")
    voice = {"provider": provider, "voiceId": env("VOICE_ID", "marin")}
    if provider == "openai":
        voice.update({"model": "gpt-4o-mini-tts", "instructions": VOICE_STYLE, "speed": 1.05,
                      # start speaking after the first short phrase instead of waiting for 30+ characters
                      "chunkPlan": {"enabled": True, "minCharacters": 12}})
    elif provider == "11labs":
        voice.update({"model": "eleven_turbo_v2_5", "stability": 0.45, "similarityBoost": 0.8, "style": 0.15,
                      "speed": 1.0, "useSpeakerBoost": True})
    elif os.environ.get("VOICE_MODEL"):
        voice["model"] = os.environ["VOICE_MODEL"]
    return voice


def tool_messages(tool_name: str) -> list[dict]:
    """Without an explicit request-start message Vapi speaks a default filler ("Hold on a sec", "Give me a moment")
    before EVERY tool call - robotic and slow on a quick field check. Silence them; checks take ~0.3 s.
    Saving is the one moment a human would narrate, so it gets a natural line."""
    if tool_name == "save_patient":
        return [{"type": "request-start", "content": "Perfect, let me get that saved for you."}]
    return [{"type": "request-start", "content": ""}]


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
            "maxTokens": 120,    # hard cap on turn length: long monologues are what callers talk over
            "messages": [{"role": "system", "content": load_prompt()}],
            "tools": [
                *[{"type": "function", "function": t, "server": server, "async": False,
                   "messages": tool_messages(t["name"])} for t in TOOLS],
                {"type": "endCall"},
            ],
        },
        "voice": build_voice(),
        "transcriber": build_transcriber(),
        # Where end-of-call reports (transcript, summary, drop reason) are delivered.
        "server": server,
        "serverMessages": ["end-of-call-report"],
        # Resilience / telephony behaviour
        "backgroundSound": "office",  # faint front-desk ambience: silence on a phone line feels robotic
        "maxDurationSeconds": 900,  # hard cap so a stuck call can't run forever
        # Don't cut people off mid phone-number; handle interruptions quickly.
        "startSpeakingPlan": {
            "waitSeconds": 0.3,
            "smartEndpointingPlan": {"provider": "vapi"},
            # A caller who says "my name is..." and pauses must NOT be cut off (this happened on the first real
            # test call): when their words end on a lead-in / filler, wait longer before the agent replies.
            "customEndpointingRules": [{
                "type": "customer",
                "regex": r"(my (first |last |full )?name is|name is|this is|it'?s|it is|i am|i'm|that'?s|and|um+|uh+|so)[\s.,]*$",
                "regexOptions": [{"type": "ignore-case", "enabled": True}],
                "timeoutSeconds": 1.5,
            }, {
                # Spelling arrives in fragments ("A-A-H." ... "H-M-E-D."): if the caller's words end on a lone letter
                # they are mid-spelling, so let them finish (2nd real call: the agent grabbed the first fragment).
                "type": "customer",
                "regex": r"(^|[\s.,\-])[a-z]([\s.,\-]*)$",
                "regexOptions": [{"type": "ignore-case", "enabled": True}],
                "timeoutSeconds": 1.8,
            }],
        },
        # Barge-in: stop the moment the caller's voice is detected (VAD), not after N transcribed words - the 3rd
        # real call showed the agent talking over a caller who was spelling (letters transcribe slowly).
        "stopSpeakingPlan": {
            "numWords": 0,
            "voiceSeconds": 0.2,
            "backoffSeconds": 0.8,
            "interruptionPhrases": ["wait", "stop", "hold on", "no", "actually", "sorry", "that's wrong", "not correct"],
        },
        "firstMessageInterruptionsEnabled": True,  # callers often start answering during the greeting
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
