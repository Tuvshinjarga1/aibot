# main.py
from fastapi import FastAPI, Request
import asyncio
from pydantic import BaseModel
import httpx
import os
from dotenv import load_dotenv
load_dotenv()

app = FastAPI()

OPENAI_API_KEY = os.getenv("OPENROUTER_API_KEY")
ASSISTANT_ID = os.getenv("ASSISTANT_API_KEY")

# 🧠 user_id -> {"thread_id": ..., "active_run": False}
user_threads = {}

class ChatwootRequest(BaseModel):
    content: str

@app.post("/api/chatwoot")
async def chatwoot_webhook(data: ChatwootRequest, request: Request):
    body = await request.json()
    print("📥 Body from Chatwoot:", body)

    try:
        # ✅ Get user_id from sender
        sender = body.get("sender") or body.get("meta", {}).get("sender")
        user_id = str(sender.get("id") if sender else "anonymous")

        # 🧠 Manage thread per user
        user_data = user_threads.get(user_id)
        if not user_data or user_data.get("active_run", False):
            thread_id = await create_thread()
            user_threads[user_id] = {"thread_id": thread_id, "active_run": False}
            print(f"✅ New thread_id: {thread_id}")
        else:
            thread_id = user_data["thread_id"]
            print(f"🧵 Using thread_id={thread_id} for user={user_id}")

        # ✉️ Send message
        content = body.get("content", data.content or "...")
        print("✉️ Sending message to assistant:", content)

        reply = await get_assistant_response(content, thread_id, user_id)
        return {"contentshdeee": reply}

    except Exception as e:
        print("⚠️ Error while handling webhook:", e)
        return {"content": "⚠️ Алдаа гарлаа. Дахин оролдоно уу."}


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


async def get_assistant_response(message: str, thread_id: str, user_id: str) -> str:
    async with httpx.AsyncClient() as client:
        # 1. Add message
        await client.post(
            f"https://api.openai.com/v1/threads/{thread_id}/messages",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "OpenAI-Beta": "assistants=v2",
                "Content-Type": "application/json"
            },
            json={"role": "user", "content": message}
        )

        # 2. Start a run
        run_res = await client.post(
            f"https://api.openai.com/v1/threads/{thread_id}/runs",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "OpenAI-Beta": "assistants=v2",
                "Content-Type": "application/json"
            },
            json={"assistant_id": ASSISTANT_ID}
        )

        run_data = run_res.json()
        run_id = run_data.get("id")
        if not run_id:
            print("❌ Run creation failed:", run_data)
            user_threads[user_id]["active_run"] = False
            return "⚠️ Хариу боловсруулах боломжгүй байна."

        # Mark run as active
        user_threads[user_id]["active_run"] = True

        # 3. Wait until the run is completed
        while True:
            status_res = await client.get(
                f"https://api.openai.com/v1/threads/{thread_id}/runs/{run_id}",
                headers={
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                    "OpenAI-Beta": "assistants=v2"
                }
            )
            status = status_res.json().get("status", "")
            if status == "completed":
                break
            elif status == "failed":
                user_threads[user_id]["active_run"] = False
                return "⚠️ Ассистант ажиллахад алдаа гарлаа."
            await asyncio.sleep(1)

        # 4. Get messages
        messages_res = await client.get(
            f"https://api.openai.com/v1/threads/{thread_id}/messages",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "OpenAI-Beta": "assistants=v2"
            }
        )
        user_threads[user_id]["active_run"] = False

        messages = messages_res.json().get("data", [])
        if not messages:
            return "⚠️ Хариу ирсэнгүй."

        return messages[0]["content"][0]["text"]["value"]