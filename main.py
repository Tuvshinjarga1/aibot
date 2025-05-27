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

# 🧠 Хэрэглэгч бүрийн thread ID-г хадгалах dict
user_threads = {}

class ChatwootRequest(BaseModel):
    content: str

@app.post("/api/chatwoot")
async def chatwoot_webhook(data: ChatwootRequest, request: Request):
    body = await request.json()
    user_id = str(body.get("sender", {}).get("id", "anonymous"))

    try:
        body = await request.json()
        print("📥 Body:", body)

        # ✅ Хэрэглэгч ID авах (Chatwoot sender ID)
        user_id = str(body.get("sender", {}).get("id", "anonymous"))
        content = body.get("content", "")

        # 🧠 Thread ID олгох
        if user_id not in user_threads:
            thread_id = await create_new_thread()
            user_threads[user_id] = thread_id
        else:
            thread_id = user_threads[user_id]

        # 🤖 Assistant-аас хариу авах
        reply = await get_assistant_response(data.content, thread_id)
        return {"content": reply}
    except Exception as e:
        print("⚠️ Алдаа:", e)
        return {"content": "Алдаа гарлаа."}


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
            json={ "role": "user", "content": message }
        )

        # 2. Run эхлүүлэх
        run_res = await client.post(
            f"https://api.openai.com/v1/threads/{thread_id}/runs",
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "OpenAI-Beta": "assistants=v2",
                "Content-Type": "application/json"
            },
            json={ "assistant_id": ASSISTANT_ID }
        )
        run_res.raise_for_status()
        run_id = run_res.json()["id"]

        # 3. Run гүйцэтгэл хүлээх (polling)
        while True:
            status_res = await client.get(
                f"https://api.openai.com/v1/threads/{thread_id}/runs/{run_id}",
                headers={
                    "Authorization": f"Bearer {API_KEY}",
                    "OpenAI-Beta": "assistants=v2",
                    "Content-Type": "application/json"
                }
            )
            status_res.raise_for_status()
            status = status_res.json()["status"]
            if status == "completed":
                break
            await asyncio.sleep(1)  # бага зэрэг хүлээнэ

        # 4. Messages-г авах
        messages_res = await client.get(
            f"https://api.openai.com/v1/threads/{thread_id}/messages",
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "OpenAI-Beta": "assistants=v2",
                "Content-Type": "application/json"
            }
        )
        messages_res.raise_for_status()
        messages = messages_res.json()["data"]
        reply = messages[0]["content"][0]["text"]["value"]
        return reply

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))  # Railway PORT heregtei
    uvicorn.run("main:app", host="0.0.0.0", port=port)
