from pydantic import BaseModel
from typing import List

class Message(BaseModel):

    role: str #system | user | assistant
    content: str

class ChatRequest(BaseModel):

    messages: List[Message]    
    conversation_id: str = None

    # Structured AI response model

class StructuredResponse(BaseModel):

        title: str
        summary: str
        steps: List[str]