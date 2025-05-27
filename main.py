# main.py
from fastapi import FastAPI, Request, Body
import asyncio
from pydantic import BaseModel
import httpx
import os
from dotenv import load_dotenv
load_dotenv()

app = FastAPI()

OPENAI_API_KEY = os.getenv("OPENROUTER_API_KEY")
ASSISTANT_ID = os.getenv("ASSISTANT_API_KEY")

user_threads = {}

@app.post("/api/chatwoot")
async def chatwoot_webhook(request: Request, body: dict = Body(...)):
    print("📥 Body from Chatwoot:", body)

    try:
        sender = body.get("sender") or body.get("meta", {}).get("sender")
        user_id = str(sender.get("id") if sender else "anonymous")
        content = body.get("content", "")
        # if body.get("content_type") != "text":
        #     print("⚠️ Non-text message ignored.")
        #     return {"content": "Текст мессеж илгээнэ үү."}

        # thread_id-г шалгах
        if user_id not in user_threads:
            thread_id = await create_thread()
            user_threads[user_id] = thread_id
            print(f"✅ New thread_id: {thread_id}")
        else:
            thread_id = user_threads[user_id]
            print(f"🧵 Using thread_id={thread_id} for user={user_id}")

        print("✉️ Sending message to assistant:", content)
        reply = "test hariu irsn shdee"
        print(reply + "testasdfsadfasdasdfasdf")
        # reply = await get_assistant_response(content, thread_id)
        return {"content": reply}

    except Exception as e:
        print("⚠️ Error while handling webhook:", e)
        return {"content": "Алдаа гарлаа."}

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
        # ✋ Check active run first
        active_runs = await client.get(
            f"https://api.openai.com/v1/threads/{thread_id}/runs",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "OpenAI-Beta": "assistants=v2"
            }
        )
        for run in active_runs.json().get("data", []):
            if run.get("status") in ["queued", "in_progress"]:
                print("⏳ Waiting for previous run to finish...")
                run_id = run["id"]
                while True:
                    status_res = await client.get(
                        f"https://api.openai.com/v1/threads/{thread_id}/runs/{run_id}",
                        headers={
                            "Authorization": f"Bearer {OPENAI_API_KEY}",
                            "OpenAI-Beta": "assistants=v2"
                        }
                    )
                    status = status_res.json().get("status")
                    if status == "completed":
                        break
                    elif status == "failed":
                        return "Өмнөх run амжилтгүй болсон."
                    await asyncio.sleep(1)

        # Step 1: Add user message
        await client.post(
            f"https://api.openai.com/v1/threads/{thread_id}/messages",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "OpenAI-Beta": "assistants=v2",
                "Content-Type": "application/json"
            },
            json={"role": "user", "content": message}
        )

        # Step 2: Start new run
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
            return "Ассистант ажиллаж чадсангүй."

        # Step 3: Poll status
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
                return "Хариу боловсруулах явцад алдаа гарлаа."
            await asyncio.sleep(1)

        # Step 4: Get messages
        messages_res = await client.get(
            f"https://api.openai.com/v1/threads/{thread_id}/messages",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "OpenAI-Beta": "assistants=v2"
            }
        )
        messages = messages_res.json().get("data", [])
        if not messages:
            return "Хариу ирсэнгүй."

        return messages[0]["content"][0]["text"]["value"]