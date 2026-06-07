import requests
import json
import time

base_url = "http://192.168.1.125:8000"

def test_flow():
    print("=== END-TO-END STREAM AND DATABASE INTEGRATION TEST ===")
    
    # 1. Create conversation
    print("\n1. Creating new conversation session...")
    response = requests.post(f"{base_url}/conversations")
    print("Response Status:", response.status_code)
    data = response.json()
    convo_id = data.get("conversation_id")
    title = data.get("title")
    print(f"Created Session ID: {convo_id}")
    print(f"Initial Title: '{title}'")
    
    if title != "New Chat":
        print("Warning: Expected initial title to be 'New Chat'")

    # 2. Send message and stream response
    print("\n2. Sending chat prompt and streaming response...")
    payload = {
        "conversation_id": convo_id,
        "messages": [
            {"role": "user", "content": "Explain in exactly 5 words what Flutter is."}
        ]
    }
    
    stream_response = requests.post(f"{base_url}/chat-stream", json=payload, stream=True)
    print("Stream Status Code:", stream_response.status_code)
    
    assistant_text = ""
    print("Streaming chunks: ", end="", flush=True)
    for chunk in stream_response.iter_content(chunk_size=None):
        if chunk:
            decoded = chunk.decode('utf-8', errors='ignore')
            # Extract content from data: headers
            for line in decoded.split("\n"):
                if line.startswith("data: "):
                    word = line[6:]
                    assistant_text += word
                    print(word, end="", flush=True)
    print("\nStream finished.")
    print("Total Assistant response:", repr(assistant_text))

    # Give the backend a brief moment to complete database save block
    time.sleep(1.0)

    # 3. Retrieve conversations list to check updated title
    print("\n3. Querying all conversations to check updated title...")
    convs_response = requests.get(f"{base_url}/conversations")
    convs_data = convs_response.json()
    
    target_convo = None
    for c in convs_data.get("conversations", []):
        if c.get("conversation_id") == convo_id:
            target_convo = c
            break
            
    if target_convo:
        print("Success! Found our conversation in history list:")
        print(f" - ID: {target_convo.get('conversation_id')}")
        print(f" - Final Title: '{target_convo.get('title')}'")
        print(f" - Updated At: {target_convo.get('updated_at')}")
        
        if target_convo.get("title") == "New Chat":
            print("\n[FAIL]: The title remains 'New Chat' in the database.")
        else:
            print("\n[SUCCESS]: The title has been successfully updated to a custom summary!")
            
        # Cleanup: delete the conversation
        requests.delete(f"{base_url}/conversations/{convo_id}")
        print("Cleanup done: Deleted test session.")
    else:
        print("[FAIL]: Could not find our created conversation in history list!")

if __name__ == "__main__":
    test_flow()
