import asyncio
import os
from motor.motor_asyncio import AsyncIOMotorClient

async def check_mongo():
    print("Testing connection to MongoDB...")
    mongo_uri = os.getenv("MONGO_URI", "mongodb://localhost:27017")
    try:
        client = AsyncIOMotorClient(mongo_uri, serverSelectionTimeoutMS=1500)
        # Try to ping the admin database
        await client.admin.command('ping')
        print("✅ SUCCESS: Successfully connected to MongoDB!")
        return True
    except Exception as e:
        print(f"❌ FAILED: Could not connect to MongoDB at {mongo_uri}.")
        print("Details:", e)
        return False

if __name__ == "__main__":
    asyncio.run(check_mongo())
