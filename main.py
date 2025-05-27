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

# Туршилтын зориулалт — production-д бол Redis/DB-р хадгална
user_threads: dict[str,str] = {}

@app.post("/api/chatwoot")
async def chatwoot_webhook(request: Request):
    body = await request.json()
    print("📥 Body from Chatwoot:", body)

    # 1) Зөвхөн хэрэглэгчээс ирсэн мессеж дээр ажиллана
    if body.get("message_type") != "incoming" or body.get("event") != "message_created":
        # Чатбот хариу өгөх шаардлагагүй event
        return {"content": ""}

    try:
        # 2) Хэрэглэгчийн ID-г meta.sender.id-аас авна
        user_id = str(body.get("meta", {})
                          .get("sender", {})
                          .get("id", "anonymous"))
        # 3) Хэрэглэгчийн бичсэн текст
        content = body.get("content", "").strip()
        if not content:
            return {"content": "⚠️ Мессеж хоосон байна."}

        # 4) Thread ID олгох/үсгэх
        if user_id not in user_threads:
            user_threads[user_id] = await create_new_thread()
        thread_id = user_threads[user_id]
        print(f"🧵 Using thread_id={thread_id} for user={user_id}")

        # 5) AI-д дамжуулж хариу авах
        reply = await get_assistant_response(content, thread_id)
        print("🤖 AI Reply:", reply)
        return {"content": reply}

    except Exception:
        traceback.print_exc()
        # Алдаа гарсан ч хоосон биш fallback өгнө
        return {"content": "💥 Хариу боловсруулах үед алдаа гарлаа."}


async def create_new_thread() -> str:
    print("➕ Creating new thread...")
    async with httpx.AsyncClient() as client:
        res = await client.post(
            "https://api.openai.com/v1/threads",
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "OpenAI-Beta": "assistants=v2",
                "Content-Type": "application/json",
            },
        )
        res.raise_for_status()
        thread_id = res.json()["id"]
        print("✅ New thread_id:", thread_id)
        return thread_id


async def get_assistant_response(message: str, thread_id: str) -> str:
    print("✉️ Sending message to assistant:", message)
    async with httpx.AsyncClient() as client:
        # а) Хэрэглэгчийн текст нэмэх
        m_res = await client.post(
            f"https://api.openai.com/v1/threads/{thread_id}/messages",
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "OpenAI-Beta": "assistants=v2",
                "Content-Type": "application/json",
            },
            json={"role": "user", "content": message},
        )
        m_res.raise_for_status()

        # б) Run эхлүүлэх
        run_res = await client.post(
            f"https://api.openai.com/v1/threads/{thread_id}/runs",
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "OpenAI-Beta": "assistants=v2",
                "Content-Type": "application/json",
            },
            json={"assistant_id": ASSISTANT_ID},
        )
        run_data = run_res.json()
        print("🛠 Run response:", run_data)
        if "id" not in run_data:
            # Алдаа заагч fallback
            return "⚠️ AI run эхлэхэд алдаа гарлаа."
        run_id = run_data["id"]

        # в) Run дуусахыг хүлээх (poll)
        print("⏳ Polling for run to complete...")
        while True:
            status_res = await client.get(
                f"https://api.openai.com/v1/threads/{thread_id}/runs/{run_id}",
                headers={
                    "Authorization": f"Bearer {API_KEY}",
                    "OpenAI-Beta": "assistants=v2",
                    "Content-Type": "application/json",
                },
            )
            status = status_res.json().get("status")
            if status == "completed":
                break
            await asyncio.sleep(1)

        # г) AI хариуг унших
        msg_res = await client.get(
            f"https://api.openai.com/v1/threads/{thread_id}/messages",
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "OpenAI-Beta": "assistants=v2",
                "Content-Type": "application/json",
            },
        )
        data = msg_res.json().get("data", [])
        if data and data[0].get("content"):
            return data[0]["content"][0]["text"]["value"]
        return "🤔 Яг хариу олдсонгүй."