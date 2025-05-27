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

# In-memory store of user_id → thread_id. Swap for Redis/DB in production.
user_threads: dict[str, str] = {}

@app.post("/api/chatwoot")
async def chatwoot_webhook(request: Request):
    body = await request.json()
    print("📥 Body from Chatwoot:", body)

    # 1) Only handle real user messages
    if body.get("event") != "message_created" or body.get("message_type") != "incoming":
        return {"content": ""}

    try:
        # 2) Extract user_id
        meta = body.get("meta", {})
        sender = meta.get("sender") or body.get("sender")
        user_id = str(sender.get("id", "anon"))

        # 3) Extract message content
        content = body.get("content", "").strip()
        # 3a) If it's a button click (template payload), handle it
        if body.get("content_type") == "template":
            payload = body.get("content_attributes", {}).get("payload")
            if payload == "choose_mn":
                return {"content": "👋 Сайн байна уу! Та монгол хэлийг сонгосон."}
            if payload == "choose_en":
                return {"content": "👋 Hello! You chose English."}
            # …add more payload cases here…
            # fallback
            return {"content": "⚠️ Unknown choice."}

        # 4) If user says “hi” or “start”, send interactive buttons
        if content.lower() in ("hi", "hello", "start"):
            return {
                "content": "Та хэлээ сонгоно уу:",
                "content_type": "template",
                "content_attributes": {
                    "payload": {
                        "type": "quick_reply",
                        "buttons": [
                            {"title": "Монгол",  "payload": "choose_mn"},
                            {"title": "English", "payload": "choose_en"}
                        ]
                    }
                }
            }

        # 5) Otherwise, treat as free-text → AI
        # 5a) Ensure we have a thread for this user
        if user_id not in user_threads:
            user_threads[user_id] = await create_new_thread()
        thread_id = user_threads[user_id]
        print(f"🧵 thread_id={thread_id} for user={user_id}")

        # 5b) Send to assistant
        reply = await get_assistant_response(content, thread_id)
        print("🤖 AI Reply:", reply)
        return {"content": reply}

    except Exception:
        traceback.print_exc()
        return {"content": "💥 Алдаа гарлаа. Дахин оролдоно уу."}


async def create_new_thread() -> str:
    print("➕ Creating new thread...")
    async with httpx.AsyncClient() as client:
        res = await client.post(
            "https://api.openai.com/v1/threads",
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "OpenAI-Beta":     "assistants=v2",
                "Content-Type":    "application/json",
            },
        )
        res.raise_for_status()
        thread_id = res.json()["id"]
        print("✅ New thread_id:", thread_id)
        return thread_id


async def get_assistant_response(message: str, thread_id: str) -> str:
    print("✉️ Sending message to assistant:", message)
    async with httpx.AsyncClient() as client:
        # a) Add the user message to the thread
        m_res = await client.post(
            f"https://api.openai.com/v1/threads/{thread_id}/messages",
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "OpenAI-Beta":     "assistants=v2",
                "Content-Type":    "application/json",
            },
            json={"role": "user", "content": message},
        )
        m_res.raise_for_status()

        # b) Kick off a run
        run_res = await client.post(
            f"https://api.openai.com/v1/threads/{thread_id}/runs",
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "OpenAI-Beta":     "assistants=v2",
                "Content-Type":    "application/json",
            },
            json={"assistant_id": ASSISTANT_ID},
        )
        run_data = run_res.json()
        print("🛠 Run response:", run_data)
        if "id" not in run_data:
            return "⚠️ AI run эхлэхэд алдаа гарлаа."
        run_id = run_data["id"]

        # c) Poll until the run completes
        print("⏳ Polling for run to complete…")
        while True:
            status_res = await client.get(
                f"https://api.openai.com/v1/threads/{thread_id}/runs/{run_id}",
                headers={
                    "Authorization": f"Bearer {API_KEY}",
                    "OpenAI-Beta":     "assistants=v2",
                    "Content-Type":    "application/json",
                },
            )
            status = status_res.json().get("status")
            if status == "completed":
                break
            await asyncio.sleep(1)

        # d) Fetch the assistant’s reply
        msg_res = await client.get(
            f"https://api.openai.com/v1/threads/{thread_id}/messages",
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "OpenAI-Beta":     "assistants=v2",
                "Content-Type":    "application/json",
            },
        )
        data = msg_res.json().get("data", [])
        if data and data[0].get("content"):
            return data[0]["content"][0]["text"]["value"]
        return "🤔 Хариу олдсонгүй."