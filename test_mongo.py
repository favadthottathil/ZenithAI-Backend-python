import asyncio
import os
from dotenv import load_dotenv

# Load environmental variables
load_dotenv()

async def test_db():
    print("Zenith AI MongoDB Integration Test")
    print("-----------------------------------")
    print("TEST: Initializing MongoDB connection...")
    try:
        from services.db_services import save_conversation, get_all_conversations, delete_conversation_by_id
        
        # Test 1: Save a mock conversation with message history logs
        print("\nTEST 1: Attempting to save a mock conversation...")
        mock_id = "test-session-12345"
        mock_messages = [
            {"role": "user", "content": "What are 3 interesting facts about the moon?"},
            {"role": "assistant", "content": "1. It has no atmosphere. 2. It is in synchronous rotation with Earth. 3. It was formed 4.5 billion years ago."}
        ]
        
        result = await save_conversation(mock_id, mock_messages)
        print("TEST 1 Success! Document saved details:", result)
        
        # Test 2: Retrieve the list of all stored conversations
        print("\nTEST 2: Attempting to fetch list of all past conversations...")
        convs = await get_all_conversations()
        print(f"TEST 2 Success! Found {len(convs)} conversation(s) in collection:")
        for c in convs:
            print(f" - ID: {c.get('conversation_id')}, Title: '{c.get('title')}'")
            
        # Test 3: Delete mock conversation to clean up the DB
        print("\nTEST 3: Attempting to delete and clean up the test document...")
        deleted = await delete_conversation_by_id(mock_id)
        print("TEST 3 Success! Deleted status:", deleted)
        
        print("\n=== SUCCESS: MongoDB database connectivity (or local fallback) and db_services are working perfectly! ===")
        
    except Exception as e:
        print("\n=== ERROR: Database integration test failed! ===")
        print("Details:", e)

if __name__ == "__main__":
    asyncio.run(test_db())

