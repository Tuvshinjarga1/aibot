# main.py
from fastapi import FastAPI, Request
import asyncio
from pydantic import BaseModel
import httpx
import os
from dotenv import load_dotenv
load_dotenv()

app = FastAPI()

API_KEY = os.getenv("OPENROUTER_API_KEY")
ASSISTANT_ID = os.getenv("ASSISTANT_API_KEY")

user_threads = {}

class ChatwootRequest(BaseModel):
    content: str

@app.post("/api/chatwoot")
async def chatwoot_webhook(data: ChatwootRequest, request: Request):
    user_input = data.content
    body = await request.json()

    # 📌 User ID олгох (хэрэглэгч тус бүрд thread үүсгэх)
    user_id = str(body.get("sender", {}).get("id", "anonymous"))
    print("📥 Body from Chatwoot:", body)

    # Thread үүсгэх/ашиглах
    thread_id = user_threads.get(user_id)
    if not thread_id:
        thread_id = await create_thread()
        user_threads[user_id] = thread_id

    # Хариу авах
    reply = await get_assistant_response(user_input, thread_id)
    return {"content": reply}

async def create_thread() -> str:
    async with httpx.AsyncClient() as client:
        res = await client.post(
            "https://api.openai.com/v1/threads",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "OpenAI-Beta": "assistants=v2",
                "Content-Type": "application/json"
            }
        )
        return res.json()["id"]

async def get_assistant_response(message: str, thread_id: str) -> str:
    async with httpx.AsyncClient() as client:
        # 1. Message нэмэх
        await client.post(
            f"https://api.openai.com/v1/threads/{thread_id}/messages",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "OpenAI-Beta": "assistants=v2",
                "Content-Type": "application/json"
            },
            json={"role": "user", "content": message}
        )

        # 2. Run эхлүүлэх
        run_res = await client.post(
            f"https://api.openai.com/v1/threads/{thread_id}/runs",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "OpenAI-Beta": "assistants=v2",
                "Content-Type": "application/json"
            },
            json={"assistant_id": ASSISTANT_ID}
        )
        run_id = run_res.json().get("id")

        # 3. Run дуусахыг хүлээх
        while True:
            status_res = await client.get(
                f"https://api.openai.com/v1/threads/{thread_id}/runs/{run_id}",
                headers={
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                    "OpenAI-Beta": "assistants=v2"
                }
            )
            status = status_res.json()["status"]
            if status == "completed":
                break
            await asyncio.sleep(1)

        # 4. Хариу авах
        messages_res = await client.get(
            f"https://api.openai.com/v1/threads/{thread_id}/messages",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "OpenAI-Beta": "assistants=v2"
            }
        )
        messages = messages_res.json()["data"]
        return messages[0]["content"][0]["text"]["value"]