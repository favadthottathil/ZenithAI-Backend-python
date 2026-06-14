import os
import uuid
import json
import logging
from datetime import datetime, timezone
from motor.motor_asyncio import AsyncIOMotorClient
from google import genai

logger = logging.getLogger("zenith_ai")

# Setup MongoDB Connection URI and database name
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = os.getenv("DB_NAME", "zenith_ai")

# Check if MongoDB is actually running and reachable synchronously on startup
def is_mongodb_available(uri: str, timeout: float = 1.0) -> bool:
    try:
        import pymongo
        # Attempt to ping MongoDB using standard MongoClient with a short timeout
        client = pymongo.MongoClient(uri, serverSelectionTimeoutMS=int(timeout * 1000))
        client.admin.command('ping')
        return True
    except Exception as e:
        logger.warning(f"MongoDB connection check failed: {e}")
        return False

# Local File-based DB Directory fallback
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "conversations")


def _validate_conversation_id(conversation_id: str) -> str:
    """
    Ensure the conversation id is a well-formed UUID.

    This blocks path-traversal (e.g. ``../../foo``) in the local-JSON file
    backend and keeps the MongoDB query key tightly constrained. Raises
    ValueError on anything that is not a canonical UUID string.
    """
    try:
        # uuid.UUID(...) rejects path separators, "..", etc. We re-stringify the
        # canonical form so the value used downstream is normalized.
        return str(uuid.UUID(conversation_id))
    except (ValueError, AttributeError, TypeError):
        raise ValueError(f"Invalid conversation_id: {conversation_id!r}")


def _conversation_file_path(conversation_id: str) -> str:
    """Resolve the on-disk path for a validated id, asserting it stays in DATA_DIR."""
    safe_id = _validate_conversation_id(conversation_id)
    path = os.path.join(DATA_DIR, f"{safe_id}.json")
    # Defense-in-depth: the resolved path must live inside DATA_DIR.
    if os.path.commonpath([os.path.realpath(path), os.path.realpath(DATA_DIR)]) != os.path.realpath(DATA_DIR):
        raise ValueError(f"Resolved path escapes data directory: {conversation_id!r}")
    return path

# Determine active DB mode
USE_MONGODB = is_mongodb_available(MONGO_URI, timeout=1.0)

if USE_MONGODB:
    logger.info("Database Mode: [MongoDB] - Successfully connected to MongoDB server!")
    client = AsyncIOMotorClient(MONGO_URI)
    db = client[DB_NAME]
    conversations_col = db["conversations"]
else:
    logger.info(f"Database Mode: [Local JSON Files] - MongoDB was not found on {MONGO_URI}.")
    logger.info(f"Falling back to resilient Local JSON File Storage at: {os.path.abspath(DATA_DIR)}")
    # Ensure the local data directory exists
    os.makedirs(DATA_DIR, exist_ok=True)

# Setup Gemini Client for Title Generation
gemini_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

async def generate_chat_title(content: str) -> str:
    """
    Generates a 3 to 5 word title for a conversation based on the provided text (like AI response content).
    Uses Gemini to summarize, falling back to basic word truncation if needed.
    """
    try:
        instruction = (
            "Summarize this text in exactly 3 to 5 words for a chat title. "
            "Do not use quotes, punctuation, or any introductory explanation phrases. "
            "Respond ONLY with the 3 to 5 words title."
        )
        final_prompt = instruction + "\n\nText: " + content
        
        # Call Gemini model
        response = gemini_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=final_prompt
        )

        title = response.text.strip().replace('"', '').replace("'", "")
        if title and len(title.split()) <= 8:
            return title
        
        # Truncation fallback if returned title is blank or too wordy
        words = content.split()
        return " ".join(words[:4]) + "..." if len(words) > 4 else content
    except Exception:
        logger.exception("Failed to generate AI title, using fallback")
        words = content.split()
        return " ".join(words[:4]) + "..." if len(words) > 4 else content

async def save_conversation(conversation_id: str, messages: list) -> dict:
    """
    Saves or updates a conversation in MongoDB or Local JSON Files with the list of messages.
    If the conversation title is absent or still has the placeholder "New Chat",
    it automatically generates a title based on the first assistant response content.
    """
    if not conversation_id:
        conversation_id = str(uuid.uuid4())
    conversation_id = _validate_conversation_id(conversation_id)

    now = datetime.now(timezone.utc)
    
    # Try to find existing conversation to check current title
    existing = await get_conversation_by_id(conversation_id)
    
    title = None
    if existing and existing.get("title") and existing.get("title") != "New Chat":
        title = existing.get("title")
    else:
        # If it's a new conversation or still has placeholder "New Chat", generate a real title
        # Check if we have an assistant response content
        assistant_msgs = [m for m in messages if (isinstance(m, dict) and m.get("role") == "assistant") or (hasattr(m, "role") and getattr(m, "role") == "assistant")]
        if assistant_msgs:
            response_content = assistant_msgs[0].get("content", "") if isinstance(assistant_msgs[0], dict) else getattr(assistant_msgs[0], "content", "")
            if response_content and response_content.strip():
                title = await generate_chat_title(response_content)
        
        # Fallback to user prompt if no assistant response is generated yet
        if not title:
            user_msgs = [m for m in messages if (isinstance(m, dict) and m.get("role") == "user") or (hasattr(m, "role") and getattr(m, "role") == "user")]
            if user_msgs:
                first_prompt = user_msgs[0].get("content", "") if isinstance(user_msgs[0], dict) else getattr(user_msgs[0], "content", "")
                if first_prompt and first_prompt.strip():
                    title = await generate_chat_title(first_prompt)
            
        if not title:
            title = "New Chat"

    # Convert Message models to raw dictionaries for storage
    raw_messages = []
    for m in messages:
        if isinstance(m, dict):
            raw_messages.append(m)
        elif hasattr(m, "dict"):
            raw_messages.append(m.dict())
        else:
            raw_messages.append({
                "role": getattr(m, "role", "user"),
                "content": getattr(m, "content", "")
            })

    document = {
        "conversation_id": conversation_id,
        "title": title,
        "messages": raw_messages,
        "updated_at": now
    }
    
    if USE_MONGODB:
        await conversations_col.update_one(
            {"conversation_id": conversation_id},
            {"$set": document},
            upsert=True
        )
    else:
        # JSON mode: save to file
        file_path = _conversation_file_path(conversation_id)
        # Format document with ISO formatted date for JSON compatibility
        json_doc = document.copy()
        json_doc["updated_at"] = now.isoformat()
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(json_doc, f, indent=4, ensure_ascii=False)
            
    return {"conversation_id": conversation_id, "title": title}

async def get_all_conversations():
    """
    Retrieves all past conversations, sorted by updated_at descending.
    """
    if USE_MONGODB:
        cursor = conversations_col.find({}, {"_id": 0, "messages": 0}).sort("updated_at", -1)
        conversations = await cursor.to_list(length=100)
        return conversations
    else:
        # JSON mode: list files and extract metadata
        conversations = []
        if not os.path.exists(DATA_DIR):
            return conversations
            
        for filename in os.listdir(DATA_DIR):
            if filename.endswith(".json"):
                file_path = os.path.join(DATA_DIR, filename)
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        # Reconstruct date object
                        updated_at_str = data.get("updated_at")
                        updated_at_dt = datetime.fromisoformat(updated_at_str) if updated_at_str else datetime.now(timezone.utc)
                        
                        conversations.append({
                            "conversation_id": data.get("conversation_id"),
                            "title": data.get("title", "New Chat"),
                            "updated_at": updated_at_dt
                        })
                except Exception:
                    logger.exception(f"Error reading file {filename}")
                    
        # Sort by updated_at descending
        conversations.sort(key=lambda x: x["updated_at"], reverse=True)
        return conversations

async def get_conversation_by_id(conversation_id: str):
    """
    Retrieves a single conversation by its ID.
    """
    try:
        conversation_id = _validate_conversation_id(conversation_id)
    except ValueError:
        return None

    if USE_MONGODB:
        convo = await conversations_col.find_one({"conversation_id": conversation_id}, {"_id": 0})
        return convo
    else:
        # JSON mode: read from file
        file_path = _conversation_file_path(conversation_id)
        if not os.path.exists(file_path):
            return None
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                # Parse date string to datetime object to match MongoDB return type exactly
                if "updated_at" in data and isinstance(data["updated_at"], str):
                    data["updated_at"] = datetime.fromisoformat(data["updated_at"])
                return data
        except Exception:
            logger.exception(f"Error reading conversation file {conversation_id}")
            return None

async def delete_conversation_by_id(conversation_id: str):
    """
    Deletes a conversation by its ID.
    """
    try:
        conversation_id = _validate_conversation_id(conversation_id)
    except ValueError:
        return False

    if USE_MONGODB:
        result = await conversations_col.delete_one({"conversation_id": conversation_id})
        return result.deleted_count > 0
    else:
        # JSON mode: delete file
        file_path = _conversation_file_path(conversation_id)
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
                return True
            except Exception:
                logger.exception(f"Error deleting conversation file {conversation_id}")
                return False
        return False
