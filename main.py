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

# Түр хадгалах (demo) — production-д бол Redis/DB-р солих
user_threads: dict[str,str] = {}

@app.post("/api/chatwoot")
async def chatwoot_webhook(request: Request):
    body = await request.json()
    print("📥 Body from Chatwoot:", body)

    # 1) Зөвхөн “incoming” message_type дээр ажиллана
    if body.get("message_type") != "incoming":
        return {}  # Бусад үйлдлийг алгасана

    # 2) Хэрэглэгчийн ID-г meta.sender эсвэл top-level sender-аас авна
    #    (чанартай нь meta.sender.id — Chatwoot v2.10+)
    user_id = (
        str(body.get("meta", {}).get("sender", {}).get("id"))
        if body.get("meta", {}).get("sender")
        else str(body.get("sender", {}).get("id", "anonymous"))
    )

    # 3) Хэрэглэгчийн бичсэн текст
    content = body.get("content", "").strip()
    if not content:
        return {"content": "⚠️ Мессеж хоосон байна."}

    # 4) Өмнө үүсгэсэн thread_id байгаа эсэхийг шалгаад үүсгэх
    if user_id not in user_threads:
        user_threads[user_id] = await create_new_thread()
    thread_id = user_threads[user_id]

    # 5) AI-д текст дамжуулж, хариу авна
    try:
        reply = await get_assistant_response(content, thread_id)
        return {"content": reply}
    except Exception:
        import traceback
        traceback.print_exc()
        return {"content": "💥 AI-д хандахад алдаа гарлаа."}


async def create_new_thread() -> str:
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
        return res.json()["id"]


async def get_assistant_response(message: str, thread_id: str) -> str:
    async with httpx.AsyncClient() as client:
        # a) Хэрэглэгчийн текст нэмэх
        await client.post(
            f"https://api.openai.com/v1/threads/{thread_id}/messages",
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "OpenAI-Beta": "assistants=v2",
                "Content-Type": "application/json",
            },
            json={"role": "user", "content": message},
        )

        # b) Run үүсгэх
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
        if "id" not in run_data:
            print("❌ Run эхлүүлэхэд алдаа:", run_data)
            return "⚠️ AI run эхлэхэд алдаа гарлаа."
        run_id = run_data["id"]

        # c) Run дуусахыг polling
        while True:
            status = (await client.get(
                f"https://api.openai.com/v1/threads/{thread_id}/runs/{run_id}",
                headers={
                    "Authorization": f"Bearer {API_KEY}",
                    "OpenAI-Beta": "assistants=v2",
                    "Content-Type": "application/json",
                },
            )).json()["status"]
            if status == "completed":
                break
            await asyncio.sleep(1)

        # d) AI хариуг унших
        messages = (await client.get(
            f"https://api.openai.com/v1/threads/{thread_id}/messages",
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "OpenAI-Beta": "assistants=v2",
                "Content-Type": "application/json",
            },
        )).json()["data"]
        return messages[0]["content"][0]["text"]["value"]