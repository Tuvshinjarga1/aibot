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

# Хэрэглэгч бүрийн thread ID-г хадгалах dict
user_threads = {}

@app.post("/api/chatwoot")
async def chatwoot_webhook(request: Request):
    body = await request.json()
    print("📥 Body from Chatwoot:", body)

    # ✅ Chatwoot structure-д тааруулж задлах
    event = body.get("event", "")
    meta_sender = body.get("meta", {}).get("sender", {})
    user_id = str(meta_sender.get("id", "anonymous"))

    # 🧠 Хариу өгөх ёстой зөв event эсэхийг шалгах
    if event != "message_created":
        return {"content": "⏳ Мессеж биш үйлдэл."}

    # 🧠 Мессежийн текст авах
    messages = body.get("messages", [])
    content = ""
    if messages and isinstance(messages, list):
        content = messages[0].get("content", "")
    else:
        content = body.get("content", "")

    if not content or content.strip() == "":
        return {"content": "⚠️ Хоосон мессеж ирсэн."}

    # 🧠 Thread ID олгох (user context хадгалах)
    if user_id not in user_threads:
        thread_id = await create_new_thread()
        user_threads[user_id] = thread_id
    else:
        thread_id = user_threads[user_id]

    # 🤖 Хариу авах
    try:
        reply = await get_assistant_response(content, thread_id)
        return {"content": reply}
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"content": "💥 Хариу боловсруулахад алдаа гарлаа."}


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
        run_data = run_res.json()
        if "id" not in run_data:
            print("❌ Run эхлүүлэхэд алдаа:", run_data)
            return "⚠️ AI run эхлэхэд алдаа гарлаа."
        run_id = run_data["id"]

        # 3. Run статус polling
        while True:
            status_res = await client.get(
                f"https://api.openai.com/v1/threads/{thread_id}/runs/{run_id}",
                headers={
                    "Authorization": f"Bearer {API_KEY}",
                    "OpenAI-Beta": "assistants=v2",
                    "Content-Type": "application/json"
                }
            )
            status_data = status_res.json()
            if status_data["status"] == "completed":
                break
            await asyncio.sleep(1)

        # 4. Message-ээс reply авах
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
