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

# Түр хадгалах dict
user_threads = {}

@app.post("/api/chatwoot")
async def chatwoot_webhook(request: Request):
    try:
        body = await request.json()
        print("📥 Body from Chatwoot:", body)

        # ✅ sender.id болон content задлах
        user_id = str(body.get("sender", {}).get("id", "anonymous"))
        content = body.get("content", "")

        if not content:
            return {"content": "⚠️ Message хоосон байна."}

        # 🧠 thread_id олгох
        if user_id not in user_threads:
            thread_id = await create_new_thread()
            user_threads[user_id] = thread_id
        else:
            thread_id = user_threads[user_id]

        reply = await get_assistant_response(content, thread_id)
        return {"content": reply}

    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"content": "💥 Алдаа гарлаа."}


async def create_new_thread() -> str:
    async with httpx.AsyncClient() as client:
        res = await client.post(
            "https://api.openai.com/v1/threads",
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "OpenAI-Beta": "assistants=v2",
                "Content-Type": "application/json"
            }
        )
        res.raise_for_status()
        return res.json()["id"]


async def get_assistant_response(message: str, thread_id: str) -> str:
    async with httpx.AsyncClient() as client:
        # 1. Message нэмэх
        await client.post(
            f"https://api.openai.com/v1/threads/{thread_id}/messages",
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "OpenAI-Beta": "assistants=v2",
                "Content-Type": "application/json"
            },
            json={"role": "user", "content": message}
        )

        # 2. Run эхлүүлэх
        run_res = await client.post(
            f"https://api.openai.com/v1/threads/{thread_id}/runs",
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "OpenAI-Beta": "assistants=v2",
                "Content-Type": "application/json"
            },
            json={"assistant_id": ASSISTANT_ID}
        )
        run_id = run_res.json()["id"]

        # 3. Polling — run дуусахыг хүлээх
        while True:
            status_res = await client.get(
                f"https://api.openai.com/v1/threads/{thread_id}/runs/{run_id}",
                headers={
                    "Authorization": f"Bearer {API_KEY}",
                    "OpenAI-Beta": "assistants=v2",
                    "Content-Type": "application/json"
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
                "Authorization": f"Bearer {API_KEY}",
                "OpenAI-Beta": "assistants=v2",
                "Content-Type": "application/json"
            }
        )
        reply = messages_res.json()["data"][0]["content"][0]["text"]["value"]
        return reply