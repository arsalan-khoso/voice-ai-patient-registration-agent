# Voice AI Agent — Patient Registration

A real phone number answers, holds a natural conversation with an LLM-powered intake coordinator
("Sam"), collects standard U.S. patient demographics, reads them back for confirmation, saves them to a
persistent database and hangs up gracefully. A REST API + small dashboard expose the stored records.

| | |
|---|---|
| **Phone number to call** | **+1 (628) 241-4309** |
| **API base URL** | https://api-production-e7d4.up.railway.app |
| **Dashboard** | https://api-production-e7d4.up.railway.app/dashboard |
| **OpenAPI docs** | https://api-production-e7d4.up.railway.app/docs |
| **Test credentials** | None needed - the REST API is open (set `API_KEY` to protect it). Two fictional seed patients exist: Jane Doe (`555-010-0123`) and Carlos Rivera. Calling from / giving `555-010-0123` triggers the "we already have a record" flow. |

---

## 1. Architecture

```
                        ┌────────────────────────── Vapi (managed voice platform) ──────────────────────────┐
  Caller ──PSTN──▶ Phone number ─▶ STT (Soniox) ─▶ LLM (GPT-4.1 + system prompt + tools) ─▶ TTS (OpenAI marin) ──┐
     ▲                                                          │  tool calls / end-of-call report              │
     └──────────────────────────── speech ◀─────────────────────┘                                               │
                                                                 │ HTTPS  POST /vapi/webhook  (X-Vapi-Secret)
                                                                 ▼
 ┌────────────────────────────── Backend container (FastAPI, Docker) ──────────────────────────────┐
 │  routers/vapi.py      telephony boundary: auth, parse Vapi payloads, dispatch tools             │
 │  voice/handlers.py    tool implementations (validate_field, lookup, save, update)               │
 │  routers/patients.py  REST API  /patients …  (envelope, status codes, API-key guard)            │
 │            └──────────────┬───────────────┘                                                     │
 │  schemas.py + validation.py   ONE validation layer used by both channels                        │
 │  services/patients.py, calls.py   ONE data-access layer used by both channels                   │
 └───────────────────────────────────────────────┬─────────────────────────────────────────────────┘
                                                 ▼
                              PostgreSQL (managed, persistent) — migrated by Alembic
                              tables: patients (soft delete), call_logs (transcripts)
```

**Separation of concerns**

| Layer | Where | Responsibility |
|---|---|---|
| Telephony / STT / TTS | Vapi (managed) | Number, audio, turn-taking, interruptions, denoising, hang-up on silence |
| LLM logic | `voice_agent/system_prompt.md`, `app/voice/tool_definitions.py` | Conversation policy, tool use |
| Voice ↔ backend | `app/routers/vapi.py`, `app/voice/handlers.py` | Auth, tool dispatch, never-silent error handling |
| API | `app/routers/patients.py` | HTTP, envelope `{ "data", "error" }`, status codes |
| Domain | `app/schemas.py`, `app/validation.py`, `app/services/*` | Validation + persistence, shared by voice **and** REST |
| Data | `app/models.py`, `alembic/` | Typed schema + CHECK constraints, migrations |

The voice agent invokes the **same service layer** the REST API uses (allowed by requirement 5), through a
webhook. Vapi calls tools server-to-server, so the backend is the single trusted writer; the LLM can never
write data the API would reject.

### Call flow
1. Vapi answers → speaks the greeting → LLM asks for name, DOB, sex, phone, address, city, state, ZIP.
2. After each risky field the LLM calls `validate_field`; if invalid it gets a **speakable, field-specific
   problem** and re-prompts only that field.
3. After the phone number: `lookup_patient_by_phone` → *"It looks like we already have a record for Jane Doe. Would you like to update your information instead?"*
4. Agent offers the optional block (insurance / emergency contact / language) — caller opts in.
5. Agent reads everything back; only after a clear "yes" it calls `save_patient(confirmed=true)`.
6. Result is relayed: *"You're all set, Jane."* → `endCall`. On DB failure the agent apologises, retries once,
   then tells the caller to call back — never silence, never a false success.
7. Vapi posts `end-of-call-report` (also on hang-ups) → transcript/summary stored in `call_logs`, linked to the patient.

---

## 2. Tech stack & justification

| Choice | Why |
|---|---|
| **Vapi** (telephony + STT + TTS + orchestration) | The PDF explicitly recommends it; gives a real number in minutes so the 3 hours go into prompt quality, validation and resilience instead of audio plumbing. Provisioned from code (`voice_agent/provision_vapi.py`) so it's reproducible. |
| **GPT-4.1** (configurable via `LLM_PROVIDER` / `LLM_MODEL`; Anthropic/Gemini also work in Vapi) | Fast, reliable tool calling, handles corrections and multilingual speech. Temperature 0.4. |
| **FastAPI + Pydantic v2** | Typed validation gives the 400/422 split cheaply, auto OpenAPI docs, async webhook + sync service layer. |
| **PostgreSQL** (prod) / SQLite (local, tests) via **SQLAlchemy 2.0** | Relational fits the fixed schema; CHECK constraints, UUID/date/timestamptz types; data survives restarts (managed volume). SQLite keeps `pytest` and first-run setup trivial. |
| **Alembic** | Schema changes are versioned; the container runs `alembic upgrade head` on boot. |
| **Docker + Railway** (`railway.toml`; Render blueprint also included) | One artifact runs identically locally (`docker compose`), on Render, Railway or Fly.io. Managed Postgres, HTTPS, health checks. |
| **Structured JSON logs to stdout** | What every platform's log viewer expects; satisfies the observability requirement. |

---

## 3. Requirements traceability (checked against the PDF)

### Functional
| PDF requirement | Status | Where |
|---|---|---|
| Real dialable U.S. number | ✅ via Vapi | `provision_vapi.py --buy-number` |
| Natural, non-IVR conversation | ✅ | `voice_agent/system_prompt.md` |
| Any LLM; varied phrasing, clarifying questions, corrections | ✅ | prompt sections *CORRECTIONS, INTERRUPTIONS, RESTARTS* |
| Read back everything & confirm/correct before saving | ✅ | prompt step 5; `save_patient` refuses without `confirmed=true` |
| Invalid data → re-prompt for *that* field | ✅ | `validate_field` tool + per-field messages in `validation.py` |
| Confirmation line + graceful end | ✅ | prompt step 6, `endCall` tool |
| All 16 data-model fields, types & validation rules | ✅ | `models.py`, `schemas.py`, `validation.py` |
| Optional fields offered ("I can also collect your insurance…") | ✅ | prompt step 4 (exact wording) |
| Persistent DB, survives restarts, schema constraints | ✅ | PostgreSQL + `models.py` CHECKs + Alembic |
| Seed data (optional) | ✅ | `SEED_DEMO_DATA=true` → `app/seed.py` |
| `GET /patients` (+ `last_name`, `date_of_birth`, `phone_number`) | ✅ | `routers/patients.py` |
| `GET/PUT/DELETE /patients/:id`, `POST /patients` | ✅ | partial PUT; DELETE = soft delete (`deleted_at`) |
| Status codes 200/201/400/404/422/500 | ✅ | `errors.py` (400 = malformed JSON / bad UUID / bad query; 422 = field validation) |
| Server-side validation independent of the agent | ✅ | Pydantic + DB CHECKs |
| Envelope `{ "data": …, "error": null }` | ✅ | every response incl. 404/500 |
| Agent persists via API/service layer, relays success/failure | ✅ | `voice/handlers.py` |
| **Bonus:** duplicate detection by phone → offer update | ✅ | `lookup_patient_by_phone`, `update_patient` (DOB identity check) |

### Non-functional
| Requirement | Status | Where |
|---|---|---|
| Deployed & callable at review time | ⏳ **you must deploy + provision** (see §5) | `Dockerfile`, `railway.toml` |
| Clean code / structure | ✅ | layered as above |
| README (setup, architecture, stack justification, env vars, limitations) | ✅ | this file |
| No hardcoded keys; input sanitisation | ✅ | env-only config; `clean_text`, markup rejection, parameterised SQL |
| Log conversations / final payload | ✅ | `patient registered via voice` log line + transcripts in `call_logs` |

### Edge cases (evaluation dimension 5)
| Scenario | Behaviour |
|---|---|
| Invalid DOB (future, impossible date, 2-digit year) | `validate_field` → specific reason → agent re-asks only DOB. Server re-validates on save. |
| Telephony drops mid-call | Nothing is saved before confirmation, so no partial/corrupt record. Vapi still sends the `end-of-call-report`; it is stored with `ended_reason` (e.g. `customer-ended-call`), `patient_id = NULL`. Silence: the agent asks "Are you still there?" after 12 s and hangs up after 40 s (Vapi `customer.speech.timeout` hooks); hard cap 15 min per call. |
| DB write fails | Handler catches it, logs the unsaved payload, returns `system_error`; the agent apologises, retries once, then asks the caller to call back. Never silence, never a false "success". Webhook/handler crash is also contained. |
| Caller wants to start over | Prompt rule: discard everything, restart from the name. Nothing was persisted yet. |
| LLM/Vapi retries `save_patient` | Idempotent per call id — one record only. |
| Same phone as an existing patient | Save is rejected with `duplicate`; agent offers to update (or `allow_duplicate_phone` for family members sharing a number). |
| Invalid webhook caller | `401` without the shared secret. |

### Bonus features included
Duplicate detection · call transcript/summary linked to the patient (`GET /patients/:id/calls`) · dashboard (`/dashboard`) ·
Spanish switching (English+Spanish transcriber + prompt rule) · 39 automated tests.
*Not done:* appointment scheduling (listed in Next Steps).

---

## 4. Prompt engineering (documented)

The full system prompt is `voice_agent/system_prompt.md`; its header comment documents the design. It is structured as
IDENTITY → HOW TO SPEAK → SPEECH FORMATTING (text is written the way TTS should say it) → LISTENING (rules learned
from real test calls: fragments, misheard names, spelling in pieces) → CHECKLIST + FLOW → TOOLS CONTRACT (a scripted
reaction to every tool outcome) → EDGE CASES → good/bad EXAMPLE dialogues. Key decisions:
- **Voice-first style rules**: 1–2 sentence turns, one question at a time, spoken-form numbers/dates, no markdown, varied acknowledgements.
- **Tools instead of memory for correctness**: dates, phones, states, ZIPs are validated by code, not by the LLM's judgement; messages are written to be spoken.
- **Mandatory read-back gate** enforced twice: by the prompt *and* by `confirmed=true` on `save_patient`.
- **Explicit rules** for corrections ("D-A-V-I-S not D-A-V-I-E-S"), out-of-order answers, interruptions, restart, silence, failure to understand.
- **Failure scripts** for every tool outcome (`errors[]`, `duplicate`, `system_error`, `verification_failed`).
- **Human touch**: HOW TO SPEAK section (contractions, brief reactions, paced to the caller, a banned list of robotic phrases) + good/bad example dialogues + OpenAI `gpt-4o-mini-tts` voice `marin` with a written delivery direction (warm front-desk persona, natural pauses, rising questions) + faint office ambience.
- **No robotic fillers**: every tool gets an explicit empty `request-start` message, otherwise Vapi says "Hold on a sec" before each check.
- Temperature 0.4; `startSpeakingPlan` waits 0.6 s + smart endpointing so callers aren't cut off mid-phone-number.

Tool schemas: `app/voice/tool_definitions.py`. Assistant config: `voice_agent/provision_vapi.py`.

---

## 5. Setup & deployment

### Local (no phone; SQLite)
```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env                                   # edit as needed
alembic upgrade head
SEED_DEMO_DATA=true uvicorn app.main:app --reload      # http://localhost:8000/docs
pytest                                                 # 39 tests
```

### Production-parity local (Postgres in Docker)
```bash
docker compose up --build          # API on :8000, Postgres volume persists across restarts
```

### Deploy to Railway (primary target)
```bash
npm i -g @railway/cli && railway login
railway init                              # new project
railway add --database postgres           # managed PostgreSQL (persistent volume)
railway add --service api                 # empty service for the app
railway variables --set ENVIRONMENT=production --set SEED_DEMO_DATA=true   --set VAPI_WEBHOOK_SECRET=$(openssl rand -hex 32)   --set 'DATABASE_URL=${{Postgres.DATABASE_URL}}'          # reference variable to the Postgres plugin
railway up                                # builds the Dockerfile (railway.toml), migrates, starts
railway domain                            # prints https://<name>.up.railway.app
```
`railway.toml` configures the Dockerfile build, the `/health` check and restart policy; Railway injects `$PORT`.
The container runs one worker by default (`WEB_CONCURRENCY`) to stay within the trial/hobby resource budget.
`render.yaml` is kept as an alternative blueprint (Render's free web tier sleeps when idle, so it is a poor fit for a phone agent).

**Voice:** default OpenAI `marin` (steerable, natural). Alternatives without code changes: `VOICE_PROVIDER=11labs VOICE_ID=sarah`, or `VOICE_PROVIDER=vapi VOICE_ID=Elliot` (cheapest).

**Cost note:** Railway has no permanent free tier: the trial gives a one-off credit; this app (one small container + Postgres) uses only cents per day. Vapi bills per call-minute from your prepaid credits; the free U.S. number itself is free.

### Provision the phone number (from your machine)
```bash
export VAPI_API_KEY=...  PUBLIC_BASE_URL=https://<your-app>.up.railway.app  VAPI_WEBHOOK_SECRET=<same as Render>
python -m voice_agent.provision_vapi --buy-number 415     # creates assistant + a free U.S. number
```
Put the printed number in the table at the top of this README. `--dry-run` prints the assistant payload; `python -m voice_agent.check_payload` validates it against Vapi's OpenAPI schema (no key needed).

### Environment variables
| Variable | Required | Purpose |
|---|---|---|
| `DATABASE_URL` | prod | `postgresql://…` (also accepts `postgres://`). Default: local SQLite file |
| `VAPI_WEBHOOK_SECRET` | prod (enforced when `ENVIRONMENT=production`) | Shared secret for `/vapi/webhook` |
| `ENVIRONMENT` | no | `production` enables strict startup checks |
| `API_KEY` | no | If set, `/patients*` require `X-API-Key` |
| `SEED_DEMO_DATA` | no | Insert 2 fictional patients when the table is empty |
| `LOG_LEVEL`, `LOG_PII` | no | `LOG_PII=false` masks phone/email/DOB/address in payload logs |
| `VAPI_API_KEY`, `PUBLIC_BASE_URL`, `VAPI_ASSISTANT_ID`, `LLM_*`, `VOICE_*`, `TRANSCRIBER_*` | provisioning script only | see `.env.example` |

### API quick reference
```bash
curl $BASE/patients?last_name=Doe
curl -X POST $BASE/patients -H 'content-type: application/json' -d '{"first_name":"Ana","last_name":"Lopez","date_of_birth":"03/09/1990","sex":"Female","phone_number":"4155550101","address_line_1":"1 Market St","city":"San Francisco","state":"CA","zip_code":"94105"}'
curl -X PUT $BASE/patients/<id> -H 'content-type: application/json' -d '{"city":"Oakland"}'
curl -X DELETE $BASE/patients/<id>      # soft delete
```

---

## 6. Known limitations & trade-offs
- **Vapi dependency**: telephony/STT/TTS are managed; outage or quota on Vapi = no calls. Chosen deliberately for the time box.
- **Confirmation is prompt-enforced + flag-enforced, not provable**: the server trusts `confirmed=true` from the LLM. A stricter design would have the server generate the read-back text itself.
- **No unique constraint on phone number**: families share phones, so duplicates are handled in the conversation (`lookup` + `duplicate` result), not by the DB. Two simultaneous calls with the same number could both insert.
- **Phone rule**: 10 digits, area code can't start with 0/1 (so `123-456-7890` is rejected). Extensions and international numbers are unsupported.
- **Names**: letters, hyphens, apostrophes and single spaces (e.g. "De La Cruz"); no periods/digits.
- **STT accuracy on spelled emails/IDs** is imperfect; the read-back is the safety net.
- **REST API auth** is a single optional shared key — no users/roles, no rate limiting. Fine for a demo, not for PHI.
- **Not HIPAA-compliant** (per the PDF): transcripts and payloads are logged in clear text unless `LOG_PII=false`. Use fictional data only.

## 6b. What has and hasn't been verified

| Verified (automated / executed) | How |
|---|---|
| REST API, validation, envelope, status codes, soft delete | 39 pytest tests, on **SQLite and real PostgreSQL 16** (`TEST_DATABASE_URL=postgresql://... pytest`) |
| Full call scenario incl. corrections, duplicate detection, update-with-DOB-check, dropped call, retried save | Scripted webhook calls against a real 2-worker server on PostgreSQL, incl. **restart persistence** and 20 parallel saves |
| Alembic migration on PostgreSQL (types + CHECK constraints) | Ran `alembic upgrade head`, inspected `pg_constraint` |
| Docker image | Built; container run in `ENVIRONMENT=production` against PostgreSQL: migrates on boot, `/health` healthy, webhook + secret guard work; refuses to start without `VAPI_WEBHOOK_SECRET` |
| Vapi assistant payload | `python -m voice_agent.check_payload` validates every tool/hook/assistant field against Vapi's **published OpenAPI schema** (this caught 4 fields Vapi would have rejected) |
| Vapi webhook message shapes | Checked against Vapi's docs/OpenAPI (`toolCallList`, `{results:[{toolCallId,result}]}`, end-of-call-report fields) |

| Live system | How |
|---|---|
| Real phone calls to the live number | Completed end-to-end registration by phone (read-back → confirmed → saved → call ended), record + transcript visible via API/dashboard |
| Persistence across a restart | Patient saved by phone at 19:56 UTC was still present after a redeploy at 19:58 UTC |
| Live REST API | Filters, 201/200/400/404/422, partial PUT, soft delete, envelope, markup rejection - checked against the Railway URL |
| Observability | `patient registered via voice` log line with the full final payload, in Railway logs |

**Not yet verified on a live call:** a mid-call spelling correction, a truly out-of-order answer, and Spanish switching
(implemented in the prompt; covered only by webhook-level tests).

### Lessons from real test calls (what changed and why)
| Observed on a real call | Fix |
|---|---|
| Caller said "my name is… Arsalan"; STT (Deepgram multi-language) returned only "My name is." | Switched STT to Soniox with domain context + vocabulary; endpointing rule waits longer after lead-ins like "my name is" |
| Spelling arrived in fragments ("A-A-H." … "A-H-M-E-D."), agent confirmed "A-A-H" | Wait longer when the caller's words end on a lone letter; prompt combines fragments; letters read back as "A, H, M, E, D" (hyphens were spoken as one word) |
| Agent kept talking when interrupted | Barge-in on voice activity (0.2 s) instead of 2 transcribed words; "wait/no/actually" stop it instantly |
| "Just a sec" before every check | Vapi's default tool filler - silenced per tool |
| "March 14, 1988" rejected three times | Date parser only accepted numeric formats; now accepts spoken forms (tested) |
| ~3 s per reply | Shorter endpointing waits, TTS chunking, address in one question, parallel validation, phone validated inside lookup |
| Offered a +92 caller ID as the U.S. phone; allowed skipping required fields | Prompt: only offer +1 caller IDs; required fields can't be skipped |

## 7. Next steps
1. Appointment scheduling after registration (mock slots table + `book_appointment` tool).
2. Server-generated read-back (removes trust in the LLM's `confirmed` flag) and per-call structured state.
3. Postgres-side partial unique index / advisory lock for duplicate phones; rate limiting; real auth for the API/dashboard.
4. Eval harness: scripted simulated callers (misheard names, corrections, restarts) run against the assistant nightly.
5. Sentry/OpenTelemetry + alerting on webhook 5xx and failed saves.

## Repo layout
```
app/                FastAPI service (routers, services, schemas, validation, models, voice tools)
alembic/            DB migrations
voice_agent/        system prompt + Vapi provisioning script
tests/              API + voice-webhook tests (pytest)
Dockerfile, docker-compose.yml, render.yaml, docker-entrypoint.sh
```
