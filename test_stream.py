import aiohttp
import asyncio

url = "http://127.0.0.1:8000/chat-stream"

async def main():

    async with aiohttp.ClientSession() as session:

        async with session.post(
            url,
            json={
                 "messages":[
                    {"role":"user","content":"Explain Flutter Bloc simply"}
                ]
            }
        ) as response:
            
            async for line in response.content:

                text = line.decode().replace("data ", "")

                print(text, end="")

asyncio.run(main())                