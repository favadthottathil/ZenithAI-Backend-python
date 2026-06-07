from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from models.chat_model import ChatRequest
from services.llm_services import generate_response, build_prompt, client
from services.db_services import (
    save_conversation,
    get_all_conversations,
    get_conversation_by_id,
    delete_conversation_by_id
)
from fastapi.responses import StreamingResponse
import asyncio
import uuid

app = FastAPI()

# Add CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
async def create_conversation():
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
async def chat(req: ChatRequest):
     result = await generate_response(req.messages)
     
     # Save history to MongoDB asynchronously
     if req.conversation_id:
         messages_list = [{"role": m.role, "content": m.content} for m in req.messages]
         messages_list.append({"role": "assistant", "content": result})
         await save_conversation(req.conversation_id, messages_list)
         
     return {"response" : result}

async def stream_genarator(messages, conversation_id=None):
     print(f"BACKEND: Received request with {len(messages)} messages (conversation_id={conversation_id})") 
     prompt = build_prompt(messages)

     structured_instruction = """
Respond naturally and clearly.
Do not use JSON.
Just explain step-by-step.
"""
     final_prompt = structured_instruction + "\n\n" + prompt

     max_retries = 6
     full_response = ""
     for attempt in range(max_retries):
          try:
               current_model = "gemini-2.5-flash" if attempt < 2 else "gemini-2.0-flash"
               
               print(f"BACKEND: Calling {current_model}... (attempt {attempt + 1})")
               stream = client.models.generate_content_stream(
                   model=current_model, 
                   contents=final_prompt,
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
                               yield f"data: {word}\n\n"
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
                           yield f"data: {word}\n\n"
                           await asyncio.sleep(0.08)
                       else:
                           yield f"data: {buffer}\n\n"
                           buffer = ""
                           
               print(f"BACKEND: Stream completed using {current_model}")
               
               # Save the conversation history in MongoDB asynchronously
               if conversation_id:
                   try:
                       messages_list = []
                       for m in messages:
                           messages_list.append({"role": m.role, "content": m.content})
                       messages_list.append({"role": "assistant", "content": full_response})
                       
                       await save_conversation(conversation_id, messages_list)
                       print(f"BACKEND: Saved history for conversation {conversation_id} cleanly to MongoDB")
                   except Exception as save_err:
                       print("BACKEND: Database save failed:", save_err)
                       
               return  # Success, exit the retry loop

          except Exception as e:
            error_str = str(e)
            print(f"BACKEND STREAM ERROR (attempt {attempt + 1}):", error_str)
            
            # If rate limited, wait with exponential backoff before retrying
            if ("429" in error_str or "RESOURCE_EXHAUSTED" in error_str) and attempt < max_retries - 1:
                wait_time = (attempt + 1) * 3 
                print(f"Rate limited on {current_model}. Waiting {wait_time}s before retry...")
                await asyncio.sleep(wait_time)
                continue
            
            if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                yield "data: Error: The AI is currently busy (Rate Limit). Please wait 30 seconds and try again.\n\n"
            else:
                yield f"data: Error: {error_str}\n\n"
            return

@app.post("/chat-stream")
async def chat_steam(req: ChatRequest):
     print(f"BACKEND: Hit /chat-stream endpoint with conversation_id={req.conversation_id}")
     return StreamingResponse(
          stream_genarator(req.messages, req.conversation_id), 
          media_type="text/event-stream"
     )