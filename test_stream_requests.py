import requests
import json

url = "http://127.0.0.1:8000/chat-stream"
data = {
    "messages": [
        {"role": "user", "content": "Explain Flutter Bloc simply in 2 sentences."}
    ]
}

print("Sending request...")
response = requests.post(url, json=data, stream=True)
print(f"Status Code: {response.statusCode if hasattr(response, 'statusCode') else response.status_code}")
print("Response Headers:", response.headers)

print("\n--- Streaming Response Chunks ---")
for chunk in response.iter_content(chunk_size=None):
    if chunk:
        print(chunk.decode('utf-8', errors='ignore'), end="", flush=True)
print("\n--- End of Stream ---")
