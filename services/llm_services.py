from google import genai
import os
import json
import re
import time
import asyncio
from dotenv import load_dotenv
from models.chat_model import StructuredResponse

load_dotenv()

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))


def build_prompt(messages):
    # Only use the last 10 messages to keep context window small and avoid rate limits
    recent_messages = messages[-10:] if len(messages) > 10 else messages
    
    prompt = ""
    for m in recent_messages:
        if m.role == "system":
            prompt += f"Instruction: {m.content}\n"
        elif m.role == "user":
            prompt += f"User: {m.content}\n"
        elif m.role == "assistant":
            prompt += f"Assistant: {m.content}\n"

    return prompt

def extract_json(text: str):

    try:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return match.group(0)
        return None

    except:
        return None


async def generate_response(messages):

    
        prompt = build_prompt(messages)

        structured_instruction = """
You MUST return ONLY valid JSON.

Do NOT write explanation.
Do NOT add extra text.

Return EXACT format:

Format:
{
  "title": string,
  "summary": string,
  "steps": ["step1", "step2"]
}
"""
        final_prompt = structured_instruction + "\n\n" + prompt

        # retry loop with exponential backoff for rate limits
        max_retries = 6
        for attempt in range(max_retries):
           try:
            # Fallback: Try 2.5 for the first 2 attempts, then fall back to 2.0
            current_model = "gemini-2.5-flash" if attempt < 2 else "gemini-2.0-flash"
            
            print(f"BACKEND: Calling {current_model}... (attempt {attempt + 1})")
            response = client.models.generate_content(
                model=current_model,
                contents=final_prompt,
            )

            raw_text = response.text
            print(f"RAW RESPONSE from {current_model}:", raw_text)

            json_text = extract_json(raw_text)
            if not json_text:
                raise ValueError("No JSON found")
            
            data = json.loads(json_text)
            validated = StructuredResponse(**data)
            return validated.dict()

           except Exception as e:
            error_str = str(e)
            print(f"Attempt {attempt + 1} failed on {current_model}: {error_str}")
            
            # If rate limited (429), wait with backoff before retrying
            if ("429" in error_str or "RESOURCE_EXHAUSTED" in error_str) and attempt < max_retries - 1:
                wait_time = (attempt + 1) * 3
                print(f"Rate limited on {current_model}. Waiting {wait_time}s before retry...")
                await asyncio.sleep(wait_time)
                continue

            return {
                "title" : "Error",
                "summary": f"Failed to generate response: {error_str}",
                "steps": []
            }