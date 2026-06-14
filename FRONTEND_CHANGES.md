# Backend Security Hardening — Frontend (Flutter) Impact Notes

The backend went through a security-hardening pass. Most of it is invisible to
the app, but a few changes can cause requests that previously succeeded to now
fail with `422`, `429`, or `413`. This doc lists what changed and what the
Flutter app should check/update.

---

## 1. Request validation is now strict (HTTP 422)

`POST /chat` and `POST /chat-stream` now validate the request body. If a
request violates any of these, the backend returns **422 Unprocessable
Entity** with a Pydantic-style error body instead of processing it.

### `messages`
- Must contain **1 to 100** messages per request.
- `role` must be exactly one of: `"system"`, `"user"`, `"assistant"`
  (case-sensitive). Any other value → 422.
- `content` is capped at **100,000 characters**. Extremely long pasted text
  will now be rejected — consider truncating or warning the user client-side.

### `attachments`
- Max **5 attachments per message**.
- `mime_type` must be one of an allow-list:
  - `image/png`
  - `image/jpeg`
  - `image/webp`
  - `image/gif`
  - `application/pdf`
  - `text/plain`
  - **Any other MIME type (e.g. `video/*`, `audio/*`, `.docx`, `.zip`, etc.)
    will be rejected with 422.** If the app currently lets users pick
    arbitrary files, either restrict the file picker to these types or show a
    friendly "unsupported file type" error on 422.
- `data` (base64 string) is capped at **10,000,000 characters**
  (~7.5 MB decoded file size). Large images/PDFs above this will be rejected —
  consider client-side compression/resizing or a size check before upload.
- `filename` capped at 255 characters.

### `conversation_id`
- If present, **must be a valid UUID** (e.g.
  `123e4567-e89b-12d3-a456-426614174000`). `null`/omitted is fine for new
  chats.
- If the app generates its own conversation IDs client-side, make sure it uses
  proper UUIDs (e.g. via the `uuid` Dart package), not arbitrary strings —
  otherwise `/chat` and `/chat-stream` will return 422.

---

## 2. Rate limiting (HTTP 429)

Per-client-IP limits are now enforced:

| Endpoint | Limit |
|---|---|
| `POST /chat` | 10 requests / minute |
| `POST /chat-stream` | 10 requests / minute |
| `POST /conversations` | 20 requests / minute |

If exceeded, the backend returns **429 Too Many Requests**. The app should:
- Catch 429 responses on these endpoints.
- Show a friendly message (e.g. "You're sending messages too quickly — please
  wait a moment and try again").
- Avoid retrying immediately in a loop (no automatic retry storms).

---

## 3. Oversized requests (HTTP 413)

Total request body is capped at **25 MB**. If a user attaches several large
files that push the whole request over this limit, the backend returns
**413 Payload Too Large** before even validating individual fields. Handle
413 similarly to the attachment-size 422 case (friendly "file too large"
message).

---

## 4. Error messages in chat responses are now generic

Previously, error responses from `/chat` and the `data: Error: ...` SSE event
from `/chat-stream` could include raw backend/Gemini exception text. Now they
return a generic message, e.g.:

- `"Failed to generate a response. Please try again in a moment."`
- `"The AI is currently busy (Rate Limit). Please wait 30 seconds and try
  again."` (unchanged — rate-limit message from Gemini still has this exact
  wording)

**Do not parse/match on specific error text** beyond the rate-limit message
above, since internal details are intentionally no longer exposed.

---

## 5. CORS (only relevant for Flutter Web)

If the app runs as **Flutter Web**, the backend's CORS policy is now an
explicit allow-list (`ALLOWED_ORIGINS` env var on the backend) instead of
`*`. If web requests start failing with CORS errors in the browser console,
the deployed web app's origin (scheme + host + port) needs to be added to the
backend's `ALLOWED_ORIGINS`. **This does not affect native Android/iOS/desktop
builds** (no `Origin` header / no CORS enforcement on native HTTP clients).

---

## Suggested frontend follow-ups

- [ ] Restrict the file/image picker to the allowed MIME types above (or
      handle 422 gracefully with a clear message).
- [ ] Add a client-side file-size check (~7.5 MB per attachment) before
      upload.
- [ ] Cap/limit very long message text client-side, or show a character
      counter near 100,000 chars.
- [ ] Ensure `conversation_id` is always a proper UUID (generated via a UUID
      package) when the app creates it locally.
- [ ] Add 429/413/422 handling in the API client layer with user-facing
      messages.
- [ ] If using Flutter Web, confirm the deployed origin is in the backend's
      `ALLOWED_ORIGINS`.
