import requests
import time

url = "http://192.168.1.125:8000/chat-stream"
data = {
    "messages": [
        {"role": "user", "content": "Explain what is Flutter in detail"}
    ]
}

print("Sending request...")
start = time.time()
response = requests.post(url, json=data, stream=True)
print(f"Status: {response.status_code}")
print(f"First byte at: {time.time() - start:.3f}s")
print("\n--- Chunks ---")
chunk_count = 0
for chunk in response.iter_content(chunk_size=None):
    if chunk:
        chunk_count += 1
        elapsed = time.time() - start
        text = chunk.decode('utf-8', errors='ignore').strip()
        print(f"[{elapsed:.3f}s] chunk #{chunk_count} ({len(text)} bytes): {text[:80]}...")
print(f"\nTotal chunks: {chunk_count}, Total time: {time.time() - start:.3f}s")
