import logging
import os

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
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
import uuid

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("zenith_ai")

# Maximum accepted request body size (bytes). Acts as a coarse circuit-breaker
# before per-field Pydantic limits are applied.
MAX_REQUEST_BODY_BYTES = 25_000_000

app = FastAPI()

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

async def stream_genarator(messages, conversation_id=None):
     logger.info(f"Received request with {len(messages)} messages (conversation_id={conversation_id})")

     # If the frontend hasn't created a conversation yet (new chat), generate
     # a real ID now and send it back as the very first event so the
     # frontend can track this conversation from the start.
     if not conversation_id:
          conversation_id = str(uuid.uuid4())
          yield f"data: \x00CONV_ID:{conversation_id}\n\n"

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

               # Buffer raw text and yield word by word with a 80ms pacing delay
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
                               yield f"data: {_encode_sse_chunk(word)}\n\n"
                               await asyncio.sleep(0.08)
                           else:
                               break

               # Yield any remaining text
               if buffer:
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
                           yield f"data: {_encode_sse_chunk(word)}\n\n"
                           await asyncio.sleep(0.08)
                       else:
                           yield f"data: {_encode_sse_chunk(buffer)}\n\n"
                           buffer = ""

               logger.info(f"Stream completed using {current_model}")

               # Save the conversation history in MongoDB asynchronously
               if conversation_id:
                   try:
                       messages_list = []
                       for m in messages:
                           messages_list.append({"role": m.role, "content": m.content})
                       messages_list.append({"role": "assistant", "content": full_response})

                       await save_conversation(conversation_id, messages_list)
                       logger.info(f"Saved history for conversation {conversation_id}")
                   except Exception as save_err:
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
                yield "data: Error: The AI is currently busy (Rate Limit). Please wait 30 seconds and try again.\n\n"
            else:
                yield "data: Error: Something went wrong while generating the response. Please try again.\n\n"
            return

def _encode_sse_chunk(text: str) -> str:
    # Escape newlines so a chunk's payload never contains "\n\n", which is
    # the event delimiter the frontend's SSE parser splits on. Without this,
    # paragraph breaks in the model's response corrupt the event framing.
    return text.replace("\r\n", "\n").replace("\n", " ")


@app.post("/chat-stream")
@limiter.limit("10/minute")
async def chat_steam(req: ChatRequest, request: Request):
     logger.info(f"Hit /chat-stream endpoint with conversation_id={req.conversation_id}")
     return StreamingResponse(
          stream_genarator(req.messages, req.conversation_id),
          media_type="text/event-stream"
     )
