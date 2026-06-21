import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from models.chat_model import ChatRequest
from services.llm_services import generate_response, build_prompt, build_contents, client
from services.db_services import (
    save_conversation,
    get_all_conversations,
    get_conversation_by_id,
    delete_conversation_by_id
)
import asyncio
import json
import urllib.request
import uuid

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("zenith_ai")

# Render's free tier spins the service down after ~15 minutes with no inbound
# traffic, which causes the next request's TLS handshake to be dropped while
# the instance cold-starts. Periodically pinging our own public URL keeps the
# instance warm and avoids that. RENDER_EXTERNAL_URL is set automatically by
# Render; fall back to the known deployed URL for local/other environments.
SELF_URL = os.getenv("RENDER_EXTERNAL_URL", "https://llm-backend-08lr.onrender.com")
KEEP_ALIVE_INTERVAL_SECONDS = 13 * 60

# Maximum accepted request body size (bytes). Acts as a coarse circuit-breaker
# before per-field Pydantic limits are applied.
MAX_REQUEST_BODY_BYTES = 25_000_000


async def _keep_alive_loop():
    while True:
        await asyncio.sleep(KEEP_ALIVE_INTERVAL_SECONDS)
        try:
            await asyncio.to_thread(urllib.request.urlopen, SELF_URL, timeout=10)
            logger.info("Keep-alive ping sent to %s", SELF_URL)
        except Exception:
            logger.exception("Keep-alive ping failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_keep_alive_loop())
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(lifespan=lifespan)

# Rate limiting (per-client-IP) to protect the paid Gemini API from abuse.
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS: explicit allow-list via env (comma-separated). Empty by default (deny all).
_allowed_origins = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=bool(_allowed_origins),
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type"],
)


@app.middleware("http")
async def limit_request_body_size(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > MAX_REQUEST_BODY_BYTES:
                return JSONResponse(status_code=413, content={"detail": "Request body too large"})
        except ValueError:
            pass
    return await call_next(request)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    return response


@app.get("/")
def home():
    return {"status": "Gemini Zenith AI backend running with MongoDB"}

# Rest API Endpoints for managing past conversation history
@app.get("/conversations")
async def list_conversations():
    """
    Returns a list of all past conversations (titles and metadata).
    """
    convs = await get_all_conversations()
    return {"conversations": convs}

@app.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: str):
    """
    Retrieves full message logs for a specific conversation session.
    """
    convo = await get_conversation_by_id(conversation_id)
    if convo:
        return convo
    return {"conversation_id": conversation_id, "title": "New Chat", "messages": []}

@app.post("/conversations")
@limiter.limit("20/minute")
async def create_conversation(request: Request):
    """
    Generates a new conversation session and returns its ID.
    """
    new_id = str(uuid.uuid4())
    await save_conversation(new_id, [])
    return {"conversation_id": new_id, "title": "New Chat"}

@app.delete("/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str):
    """
    Removes a conversation session from MongoDB database.
    """
    success = await delete_conversation_by_id(conversation_id)
    return {"success": success}

# Chat endpoints
@app.post("/chat")
@limiter.limit("10/minute")
async def chat(req: ChatRequest, request: Request):
     result = await generate_response(req.messages)

     # Save history to MongoDB asynchronously
     if req.conversation_id:
         messages_list = [{"role": m.role, "content": m.content} for m in req.messages]
         messages_list.append({"role": "assistant", "content": json.dumps(result)})
         await save_conversation(req.conversation_id, messages_list)

     return {"response" : result}

async def generate_reply_words(messages, conversation_id):
     """
     Transport-agnostic generation core: calls Gemini, paces the response out
     word-by-word, and persists the finished conversation. Yields plain text
     words (no SSE/WS framing) so any transport can wrap them as it likes.
     Raises on unrecoverable errors; the caller decides how to report them.
     """
     prompt = build_prompt(messages)

     structured_instruction = """
Respond naturally and clearly.
Do not use JSON.
Just explain step-by-step.
"""
     final_prompt = structured_instruction + "\n\n" + prompt
     contents = build_contents(messages, final_prompt)

     max_retries = 6
     full_response = ""
     for attempt in range(max_retries):
          current_model = "gemini-2.5-flash" if attempt < 2 else "gemini-2.0-flash"
          try:
               logger.info(f"Calling {current_model}... (attempt {attempt + 1})")
               stream = client.models.generate_content_stream(
                   model=current_model,
                   contents=contents,
               )

               # Buffer raw text and yield word by word with an 80ms pacing delay
               buffer = ""
               for chunk in stream:
                   if hasattr(chunk, "text") and chunk.text:
                       full_response += chunk.text
                       buffer += chunk.text

                       while True:
                           boundary_idx = -1
                           has_non_space = False
                           for idx, char in enumerate(buffer):
                               if char in (' ', '\n', '\r', '\t'):
                                   if has_non_space:
                                       boundary_idx = idx
                                       break
                               else:
                                   has_non_space = True

                           if boundary_idx != -1:
                               word = buffer[:boundary_idx]
                               buffer = buffer[boundary_idx:]
                               yield word
                               await asyncio.sleep(0.08)
                           else:
                               break

               # Yield any remaining text
               while buffer:
                   boundary_idx = -1
                   has_non_space = False
                   for idx, char in enumerate(buffer):
                       if char in (' ', '\n', '\r', '\t'):
                           if has_non_space:
                               boundary_idx = idx
                               break
                       else:
                           has_non_space = True
                   if boundary_idx != -1:
                       word = buffer[:boundary_idx]
                       buffer = buffer[boundary_idx:]
                       yield word
                       await asyncio.sleep(0.08)
                   else:
                       yield buffer
                       buffer = ""

               logger.info(f"Stream completed using {current_model}")

               # Save the conversation history in MongoDB asynchronously
               try:
                   messages_list = []
                   for m in messages:
                       messages_list.append({"role": m.role, "content": m.content})
                   messages_list.append({"role": "assistant", "content": full_response})

                   await save_conversation(conversation_id, messages_list)
                   logger.info(f"Saved history for conversation {conversation_id}")
               except Exception:
                   logger.exception(f"Database save failed for conversation {conversation_id}")

               return  # Success, exit the retry loop

          except Exception as e:
            error_str = str(e)
            logger.exception(f"Stream error on {current_model} (attempt {attempt + 1})")

            # If rate limited, wait with exponential backoff before retrying
            if ("429" in error_str or "RESOURCE_EXHAUSTED" in error_str) and attempt < max_retries - 1:
                wait_time = (attempt + 1) * 3
                logger.info(f"Rate limited on {current_model}. Waiting {wait_time}s before retry...")
                await asyncio.sleep(wait_time)
                continue

            if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                raise RuntimeError("The AI is currently busy (Rate Limit). Please wait 30 seconds and try again.") from e
            raise RuntimeError("Something went wrong while generating the response. Please try again.") from e


# WebSocket connection start times per client IP, used for a simple sliding-window
# rate limit (slowapi's decorator only covers HTTP routes, not WebSocket routes).
_ws_rate_limit_window: dict[str, list[float]] = {}
WS_RATE_LIMIT_PER_MINUTE = 10


def _ws_rate_limited(client_host: str) -> bool:
    now = asyncio.get_event_loop().time()
    window_start = now - 60
    timestamps = [t for t in _ws_rate_limit_window.get(client_host, []) if t > window_start]
    timestamps.append(now)
    _ws_rate_limit_window[client_host] = timestamps
    return len(timestamps) > WS_RATE_LIMIT_PER_MINUTE


@app.websocket("/ws/chat-stream")
async def ws_chat_stream(ws: WebSocket):
    origin = ws.headers.get("origin")
    if _allowed_origins and origin and origin not in _allowed_origins:
        await ws.close(code=1008)
        return

    await ws.accept()

    client_host = ws.client.host if ws.client else "unknown"
    if _ws_rate_limited(client_host):
        await ws.send_json({"type": "error", "message": "You're sending messages too quickly. Please wait a moment and try again."})
        await ws.close(code=1008)
        return

    try:
        init = await ws.receive_json()
    except (json.JSONDecodeError, WebSocketDisconnect):
        return

    if init.get("action") != "start":
        await ws.send_json({"type": "error", "message": "Expected a 'start' action."})
        await ws.close(code=1003)
        return

    try:
        req = ChatRequest(messages=init.get("messages", []), conversation_id=init.get("conversation_id"))
    except ValidationError:
        await ws.send_json({"type": "error", "message": "Your message couldn't be sent. Please check your input and attachments."})
        await ws.close(code=1003)
        return

    conversation_id = req.conversation_id
    logger.info(f"WS chat-stream started (conversation_id={conversation_id})")

    # New chat: mint a real ID up front and tell the client immediately.
    if not conversation_id:
        conversation_id = str(uuid.uuid4())
        await ws.send_json({"type": "conversation_id", "conversation_id": conversation_id})

    generator = generate_reply_words(req.messages, conversation_id)
    generation_task = asyncio.ensure_future(generator.__anext__())
    receive_task = asyncio.ensure_future(ws.receive_json())

    try:
        while True:
            done, _pending = await asyncio.wait(
                {generation_task, receive_task}, return_when=asyncio.FIRST_COMPLETED
            )

            if receive_task in done:
                try:
                    incoming = receive_task.result()
                except (WebSocketDisconnect, json.JSONDecodeError):
                    generation_task.cancel()
                    break
                if incoming.get("action") == "stop":
                    generation_task.cancel()
                    break
                receive_task = asyncio.ensure_future(ws.receive_json())
                continue

            if generation_task in done:
                try:
                    word = generation_task.result()
                except StopAsyncIteration:
                    await ws.send_json({"type": "done"})
                    break
                except RuntimeError as e:
                    await ws.send_json({"type": "error", "message": str(e)})
                    break
                await ws.send_json({"type": "chunk", "data": word})
                generation_task = asyncio.ensure_future(generator.__anext__())
    except WebSocketDisconnect:
        generation_task.cancel()
    finally:
        receive_task.cancel()
        await generator.aclose()
        try:
            await ws.close()
        except Exception:
            pass
