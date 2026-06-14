import uuid

from pydantic import BaseModel, Field, field_validator
from typing import List, Optional, Literal

# --- Size / count limits (defense against memory-exhaustion DoS) ---
# base64 inflates bytes by ~4/3, so this cap allows roughly ~7.5 MB decoded per file.
MAX_ATTACHMENT_B64_LEN = 10_000_000
MAX_ATTACHMENTS_PER_MESSAGE = 5
MAX_CONTENT_LEN = 100_000
MAX_MESSAGES = 100

# Attachment MIME types we are willing to forward to the model.
ALLOWED_MIME_TYPES = {
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/gif",
    "application/pdf",
    "text/plain",
}


class Attachment(BaseModel):

    type: Literal["image", "document"]
    mime_type: str
    filename: str = Field(max_length=255)
    data: str = Field(max_length=MAX_ATTACHMENT_B64_LEN)  # base64-encoded file bytes

    @field_validator("mime_type")
    @classmethod
    def _validate_mime_type(cls, v: str) -> str:
        if v not in ALLOWED_MIME_TYPES:
            raise ValueError(f"Unsupported mime_type: {v}")
        return v


class Message(BaseModel):

    role: Literal["system", "user", "assistant"]
    content: str = Field(max_length=MAX_CONTENT_LEN)
    attachments: Optional[List[Attachment]] = Field(
        default=None, max_length=MAX_ATTACHMENTS_PER_MESSAGE
    )


class ChatRequest(BaseModel):

    messages: List[Message] = Field(min_length=1, max_length=MAX_MESSAGES)
    conversation_id: Optional[str] = None

    @field_validator("conversation_id")
    @classmethod
    def _validate_conversation_id(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        try:
            return str(uuid.UUID(v))
        except (ValueError, AttributeError, TypeError):
            raise ValueError("conversation_id must be a valid UUID")


# Structured AI response model
class StructuredResponse(BaseModel):

    title: str
    summary: str
    steps: List[str]
