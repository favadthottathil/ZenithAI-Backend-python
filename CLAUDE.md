# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

FastAPI backend ("Zenith AI") that proxies chat requests to Google Gemini
(`google-genai`) and persists conversation history to MongoDB, with a local
JSON-file fallback when MongoDB is unreachable. Consumed by a separate Flutter
frontend (see `FRONTEND_CHANGES.md` for the contract/impact notes shared with
that team).

## Running locally

```bash
# Activate venv (Windows)
.venv\Scripts\activate

pip install -r requirements.txt
uvicorn main:app --reload
```

Requires a `.env` file (see `.env.example`):
- `GEMINI_API_KEY` — required, Gemini API key
- `ALLOWED_ORIGINS` — comma-separated CORS allow-list (empty = deny all cross-origin)
- `MONGO_URI` / `DB_NAME` — optional; falls back to local JSON storage in `data/conversations/` if MongoDB is unreachable at startup

## Tests

There is no test runner config (pytest not in requirements). The `test_*.py`
files at the repo root are standalone scripts run directly against a live
server (some hardcode a LAN IP/port), e.g.:

```bash
python test_stream.py
python test_e2e_stream.py
```

## Deployment

Deployed to Render (`render.yaml`): `uvicorn main:app --host 0.0.0.0 --port $PORT`.
Python version pinned in `runtime.txt` (3.11.11).

## Architecture

- `main.py` — FastAPI app: routes, middleware, SSE streaming generator.
- `models/chat_model.py` — Pydantic request/response schemas and all input
  validation limits (message/attachment counts, size caps, allowed MIME
  types, UUID validation for `conversation_id`).
- `services/llm_services.py` — Gemini client, prompt building
  (`build_prompt`/`build_contents`), and `generate_response` (non-streaming,
  structured JSON output).
- `services/db_services.py` — conversation persistence; auto-detects MongoDB
  at import time (`USE_MONGODB`) and falls back to JSON files under
  `data/conversations/`. Also generates AI-based chat titles via Gemini.

### Key request flows

- `POST /chat` — non-streaming. Calls `generate_response`, which prompts
  Gemini to return strict JSON matching `StructuredResponse`
  (`title`/`summary`/`steps`), with retry/backoff and a `gemini-2.5-flash` →
  `gemini-2.0-flash` model fallback after 2 attempts.
- `POST /chat-stream` — SSE streaming via `stream_genarator` in `main.py`.
  Uses a *different* prompt (plain natural-language instruction, no JSON) and
  streams Gemini output word-by-word with an 80ms pacing delay. If no
  `conversation_id` is supplied, generates one and sends it as a special first
  SSE event: `data: \x00CONV_ID:<uuid>\n\n`. SSE chunk text has newlines
  collapsed to spaces (`_encode_sse_chunk`) because `\n\n` is the event
  delimiter the frontend parses on. Same model fallback/retry logic as `/chat`.
- Both endpoints share the same 10-message context window (`build_prompt`/
  `build_contents` use only the last 10 messages) and accept the same
  attachment handling (base64 → `types.Part.from_bytes`).
- Conversation persistence (`save_conversation`) auto-generates a title from
  the first assistant response (or first user message) via Gemini when a
  conversation has no title or still has the placeholder `"New Chat"`.

### Security constraints (intentional, don't relax casually)

- Per-IP rate limits via `slowapi`: `/chat` and `/chat-stream` = 10/min,
  `POST /conversations` = 20/min.
- Request body capped at 25MB (`MAX_REQUEST_BODY_BYTES` in `main.py`).
- `conversation_id` must be a canonical UUID — validated both in
  `ChatRequest` (Pydantic) and again in `services/db_services.py`
  (`_validate_conversation_id`/`_conversation_file_path`) to prevent path
  traversal in the JSON-file fallback.
- Error messages returned to clients are intentionally generic (no raw
  exception text), except the specific rate-limit message
  `"The AI is currently busy (Rate Limit). Please wait 30 seconds and try
  again."`, which the frontend matches on.
- CORS is an explicit allow-list (`ALLOWED_ORIGINS`), not `*`.

## Type checking

`pyrefly.toml` configures Pyrefly to use `.venv/Scripts/python.exe`.
