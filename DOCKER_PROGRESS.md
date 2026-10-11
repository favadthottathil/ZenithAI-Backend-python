# Docker progress: Zenith AI backend (Project B)

Source of truth for the resume. Tick a stage only after its exit check was run and the output was seen.

## Stages

- [x] B1. Read the app (2026-10-09): learned the app does not crash without MongoDB, it silently falls back to JSON files in the container filesystem, which disappear with the container
- [x] B2. Dockerfile (2026-10-10): python:3.11.17-slim-bookworm, uvicorn on 127.0.0.1 gave `Empty reply from server` through the published port, `--host 0.0.0.0` fixed it; Postman WebSocket got conversation_id, chunk, done through the container, with the Gemini key passed by `--env-file` at run time
- [x] B3. Compose + Flutter client (2026-10-10): `compose.yaml` (service `api`, `env_file`, port 8000, restart policy) started with `docker compose up -d --build`; physical Android phone on the same Wi-Fi ran the Flutter app with `--dart-define=API_BASE_URL=http://<PC LAN IP>:8000` and streamed a Gemini answer through the container (log: WebSocket accepted, Gemini 200, history saved)
- [x] B4. Dev vs prod, secrets (2026-10-11): fake key in `ENV` of a throwaway image was readable with `docker history --no-trunc` (BuildKit also warned `SecretsUsedInArgOrEnv`), image deleted; real image checked: 0 `GEMINI` matches in history, 0 in env without `--env-file`, 0 `AIza` matches in /app. `compose.override.yaml` (bind mounts of main.py, models/, services/ + `--reload`) edited a response text and it showed up without a rebuild; `docker compose -f compose.yaml up` runs the image as built, no reloader
- [x] B5. Harden (2026-10-11): `USER 10001` (`whoami` = appuser; only /app/data is writable by it), `.dockerignore`, Python-stdlib HEALTHCHECK (`docker compose ps` shows `healthy`; a deliberately broken container on port 9000 went `unhealthy` with `Connection refused` in `docker inspect`), compose limits 1 CPU / 512M (idle use ~67 MiB), `cap_drop: ALL`, `no-new-privileges`. hadolint: first run DL3066 only (non-numeric USER), clean (exit 0) after `USER 10001`. Trivy HIGH+CRITICAL: 59 findings = 57 Debian packages with no fix available yet + 2 in setuptools/wheel vendored copies; after `pip uninstall -y setuptools wheel` the 2 are gone, 57 Debian remain (unfixable by rebuilding). Image size unchanged at 306 MB (disk)
- [ ] B6. CI to GHCR (stretch: multi-arch)
- [ ] B7. Deploy behind a reverse proxy with WebSocket (optional)

## Measured numbers

| What | Before | After |
|---|---|---|
| Image size (disk / content) | 307 MB / (not shown) (first build, no .dockerignore, python:3.11.17-slim-bookworm) | 306 MB / 69.8 MB (after .dockerignore; single stage) |
| Build context | 179.46 kB (no .dockerignore) | 716 B (after .dockerignore) |

Note: .dockerignore barely changed the image size (the base image and pip packages dominate). Its value here is safety: without it the image contained the 4 real chat history files (and would contain .env).

## B1 findings (2026-10-09)

- Entry point: `main:app` (file `main.py` at repo root). Start: `uvicorn main:app --host 0.0.0.0 --port $PORT` (from `render.yaml`).
- Python: `runtime.txt` pins 3.11.11 (what Render runs). Local `__pycache__` shows 3.13.
- Dependencies: `requirements.txt`, nine packages, **none pinned** (fastapi, uvicorn, websockets, google-genai, motor, python-dotenv, pydantic, pymongo, slowapi).
- Env vars read by the code: `GEMINI_API_KEY` (required), `ALLOWED_ORIGINS`, `MONGO_URI` (default `mongodb://localhost:27017`), `DB_NAME` (default `zenith_ai`), `RENDER_EXTERNAL_URL` (keep-alive self-ping target).
- Routes: `GET /` (health-ish), `/conversations` CRUD, `POST /chat`, `POST /chat-stream` (SSE), **`WS /ws/chat-stream`** (WebSocket).
- WebSocket protocol: client sends `{"action":"start","messages":[...],"conversation_id":?}`; server sends `conversation_id`, `chunk`, `done`, `error`; client can send `{"action":"stop"}`.
- Database: MongoDB is **optional**. At import time the app pings `MONGO_URI` with a short timeout and, if it fails, falls back to JSON files in `data/conversations/`.
- External dependency: Gemini API over the internet (needs outbound network from the container).
- Things that matter for Docker:
  - `data/conversations/*.json` (4 files) is tracked in git, so `COPY . .` would bake real chat history into the image.
  - Fallback JSON storage lives inside the container filesystem, so it disappears with the container unless a volume is mounted.
  - Default `MONGO_URI` is `localhost`; inside a container that means the container itself.
  - `lifespan` starts a loop that pings the public Render URL every 13 min; harmless locally, but surprising.
  - WebSocket rate limit state is in-process memory (`_ws_rate_limit_window`), so it assumes a single worker.

## What was different from Project A

(Fill in as stages are completed. This note is the best interview material.)

- B2: no database to wait for (Project A needed healthchecks for Postgres). The app depends on an external API (Gemini) and a secret key instead, so the network question is "can the container reach the internet" and the secret goes in at run time.
- B2: the Python-specific gotchas were `PYTHONUNBUFFERED` (logs appear immediately) and the uvicorn host: `127.0.0.1` inside a container is unreachable even with `-p`. Project A did not show this problem. (Unchecked: verify in Project A's code how `listen` is called before claiming why.)
- B2: `.dockerignore` mattered for safety, not size (307 MB to 306 MB, build context 179.46 kB to 716 B). Without it the image held 4 real chat history files and would hold `.env`.
- B3: first compose.yaml draft had the service keys at the top level; `docker compose config` rejected it (`additional properties 'build', ... not allowed`) until they were nested under `services: api:`. `docker compose config` also prints `.env` values, so never paste its output.
- B3: reaching the container from a phone: `localhost` on the phone is the phone, so use the PC's LAN IP (emulator: `10.0.2.2`). Phone browser said "This site can't provide a secure connection" because it upgraded to https; the container only speaks plain http. Flutter app: debug builds allow cleartext, release builds are forced to https (by design). Docker Desktop on Windows hides the phone's real IP (the log shows the bridge gateway `172.19.0.1`).
- B4: dev uses bind mounts of only the source files (mounting the whole repo would hide the image's `/app/data` and write chat JSON into the git-tracked `./data`). Plain `uvicorn` uses StatReload (polling), which works across Windows bind mounts. `compose.override.yaml` is merged automatically, so prod parity needs `-f compose.yaml`. The prod rebuild reported `CACHED` for `COPY . .` and the same image digest, which shows the image only changes when the files copied into it change.
- B5: a `docker exec ... kill -STOP 1` does nothing (signals to PID 1 from inside its own PID namespace are ignored); to test a healthcheck, make the probe fail for real (app on another port). Docker only marks a container `unhealthy`, it does not restart it. The slim image has no curl, so the healthcheck uses `python -c urllib...` and probes 127.0.0.1 (correct here because it runs inside the container).
- B5: Python-specific scan finding: the vulnerable code was in setuptools/wheel shipped with the base image, not in my dependencies; removing them after `pip install` is the Python equivalent of not shipping build tools.
- B3: a local container keeps pinging the production Render URL (`_keep_alive_loop` in main.py), which is a side effect of running the same code locally.
- B2: the app silently falls back to JSON files when Mongo is unreachable; in a container those files vanish with the container.
